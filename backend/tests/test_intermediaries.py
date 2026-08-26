"""Реестр посредников и отсев площадок до загрузки страниц."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_intermediaries.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Intermediary
from app.services.intermediaries import (
    domain_label,
    is_intermediary,
    normalize_domain,
    seed_intermediaries,
    split_by_intermediary,
)


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_intermediaries.db"):
        os.remove("test_intermediaries.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_intermediaries.db"):
        os.remove("test_intermediaries.db")


def _auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_domain_normalisation_accepts_what_people_actually_paste():
    assert normalize_domain("https://www.ECHEMI.com/product/1") == "echemi.com"
    assert normalize_domain("www.made-in-china.com") == "made-in-china.com"
    assert normalize_domain("guidechem.com:443") == "guidechem.com"
    assert normalize_domain("  Alibaba.com  ") == "alibaba.com"


def test_subdomains_of_a_marketplace_are_covered():
    """У площадок поддомен на компанию — обычное дело, правило шире домена."""
    domains = {"echemi.com", "made-in-china.com"}
    assert is_intermediary("https://shop.echemi.com/us2021", domains)
    assert is_intermediary("https://fortunegrowth.en.made-in-china.com/x", domains)
    assert not is_intermediary("https://nbinno.com/article/betaine", domains)


def test_lookalike_domain_is_not_matched():
    """Похожая метка — не та же метка."""
    assert not is_intermediary("https://notechemi.com/p", {"echemi.com"})
    assert not is_intermediary("https://echemi-shop.ru/p", {"echemi.com"})


def test_label_is_taken_before_the_zone_not_before_the_first_dot():
    """У площадок поддомен принадлежит продавцу, а не площадке."""
    assert domain_label("fortunegrowth.en.made-in-china.com") == "made-in-china"
    assert domain_label("shop.echemi.com") == "echemi"
    # Составная зона: иначе меткой оказалось бы «com».
    assert domain_label("lookchem.com.cn") == "lookchem"
    assert domain_label("www.guidechem.com") == "guidechem"


def test_mirrors_in_other_zones_are_caught():
    """Ровно тот случай, который проскочил на стенде: lookchem.cn."""
    registry = {"lookchem.com"}
    assert is_intermediary("https://www.lookchem.cn/cas_107-43-7.html", registry)
    assert is_intermediary("https://china.lookchem.com.cn/x", registry)
    assert is_intermediary("https://www.lookchem.com/cas-107/107-43-7.html", registry)


def test_split_keeps_order_and_separates_platforms():
    results = [
        {"url": "https://plant.example/betaine"},
        {"url": "https://www.chemicalbook.com/Product.htm"},
        {"url": "https://factory.cn/product"},
    ]
    direct, intermediaries = split_by_intermediary(results, {"chemicalbook.com"})
    assert [item["url"] for item in direct] == [
        "https://plant.example/betaine",
        "https://factory.cn/product",
    ]
    assert len(intermediaries) == 1


def test_seed_is_idempotent_and_keeps_user_edits(client):
    with SessionLocal() as db:
        before = db.query(Intermediary).count()
        assert before > 0, "стартовый список должен заполняться при запуске"
        item = db.query(Intermediary).filter(
            Intermediary.domain == "echemi.com"
        ).one()
        item.name = "Изменено закупщиком"
        item.is_active = False
        db.commit()

    with SessionLocal() as db:
        assert seed_intermediaries(db) == 0
        item = db.query(Intermediary).filter(
            Intermediary.domain == "echemi.com"
        ).one()
        assert item.name == "Изменено закупщиком"
        assert item.is_active is False
        # Возвращаем запись, иначе следующий тест увидит отключённую площадку.
        item.name = "ECHEMI"
        item.is_active = True
        db.commit()


def test_registry_is_editable_by_a_buyer_and_read_only_for_an_auditor(client):
    buyer = _auth(client, "ivanov")
    auditor = _auth(client, "auditor")

    assert (
        client.post(
            "/intermediaries",
            headers=auditor,
            json={"domain": "audit-denied.example", "name": "Только чтение"},
        ).status_code
        == 403
    )

    listed = client.get("/intermediaries", headers=buyer)
    assert listed.status_code == 200
    assert any(item["domain"] == "echemi.com" for item in listed.json())

    created = client.post(
        "/intermediaries",
        headers=buyer,
        json={
            "domain": "https://www.NewShop.example/catalog",
            "name": "Новая площадка",
            "kind": "reseller",
        },
    )
    assert created.status_code == 201
    assert created.json()["domain"] == "newshop.example", "домен нормализуется"

    duplicate = client.post(
        "/intermediaries",
        headers=buyer,
        json={"domain": "newshop.example", "name": "Она же"},
    )
    assert duplicate.status_code == 409

    item_id = created.json()["id"]
    patched = client.patch(
        f"/intermediaries/{item_id}",
        headers=buyer,
        json={
            "domain": "renamed-shop.example",
            "name": "Исправленная площадка",
            "kind": "catalog",
            "notes": "Проверено закупщиком",
            "is_active": False,
        },
    )
    assert patched.status_code == 200
    assert patched.json()["domain"] == "renamed-shop.example"
    assert patched.json()["name"] == "Исправленная площадка"
    assert patched.json()["kind"] == "catalog"
    assert patched.json()["notes"] == "Проверено закупщиком"
    assert patched.json()["is_active"] is False

    assert (
        client.patch(
            f"/intermediaries/{item_id}",
            headers=auditor,
            json={"name": "Недопустимое изменение"},
        ).status_code
        == 403
    )
    assert client.delete(f"/intermediaries/{item_id}", headers=auditor).status_code == 403
    assert client.delete(f"/intermediaries/{item_id}", headers=buyer).status_code == 204


def test_unknown_kind_is_rejected(client):
    head = _auth(client, "petrova")
    response = client.post(
        "/intermediaries",
        headers=head,
        json={"domain": "x.example", "name": "X", "kind": "выдуманный"},
    )
    assert response.status_code == 422


# --- отметка посредника из карточки результата (MEET2-08) ---


def _mark(client, headers, **kw):
    body = {
        "url": "https://trader-demo.example/catalog/betaine",
        "name": "Trader Demo",
        "reason": "Перепродаёт чужой товар, своего производства нет",
    }
    body.update(kw)
    return client.post("/intermediaries/mark", headers=headers, json=body)


def _find(client, headers, domain: str):
    listed = client.get("/intermediaries", headers=headers).json()
    return next((item for item in listed if item["domain"] == domain), None)


def test_marking_records_who_why_and_from_which_result(client):
    """Правило меняет будущие поиски всех — значит должно быть предъявимым."""
    buyer = _auth(client, "ivanov")
    response = _mark(client, buyer, rfq_id=1)
    assert response.status_code == 201, response.text
    item = response.json()

    assert item["domain"] == "trader-demo.example"
    assert item["is_active"] is True
    assert item["reason"].startswith("Перепродаёт")
    # Доказательство отметки: исходный результат и запрос, где его увидели.
    assert item["source_url"].endswith("/catalog/betaine")
    assert item["source_rfq_id"] == 1
    assert item["added_by_name"] == "Иван Иванов"


def test_marking_the_same_domain_twice_does_not_duplicate_it(client):
    buyer = _auth(client, "ivanov")
    _mark(client, buyer, url="https://dup-demo.example/a")
    second = _mark(
        client, buyer, url="https://dup-demo.example/b", reason="Уточнённая причина"
    )
    assert second.status_code == 201

    listed = client.get("/intermediaries", headers=buyer).json()
    matches = [item for item in listed if item["domain"] == "dup-demo.example"]
    assert len(matches) == 1
    assert matches[0]["reason"] == "Уточнённая причина"


def test_reason_is_required(client):
    """Без причины правило нельзя ни проверить, ни оспорить."""
    buyer = _auth(client, "ivanov")
    response = client.post(
        "/intermediaries/mark",
        headers=buyer,
        json={"url": "https://no-reason.example/x", "reason": ""},
    )
    assert response.status_code == 422


def test_auditor_can_look_but_not_mark(client):
    auditor = _auth(client, "auditor")
    assert _mark(client, auditor, url="https://denied.example/x").status_code == 403
    assert client.get("/intermediaries", headers=auditor).status_code == 200


def test_head_and_admin_can_mark_too(client):
    for username, domain in (("petrova", "head-demo.example"), ("admin", "admin-demo.example")):
        headers = _auth(client, username)
        assert _mark(client, headers, url=f"https://{domain}/x").status_code == 201


def test_a_used_rule_is_deactivated_not_erased(client):
    """Прошлые поиски шли с этим правилом — стереть его значит соврать."""
    buyer = _auth(client, "ivanov")
    created = _mark(client, buyer, url="https://undo-demo.example/x").json()

    assert client.delete(f"/intermediaries/{created['id']}", headers=buyer).status_code == 204

    item = _find(client, buyer, "undo-demo.example")
    assert item is not None, "запись обязана остаться в реестре"
    assert item["is_active"] is False
    # Видно, кто и когда отменил — иначе отмена сама по себе неотличима.
    assert item["deactivated_by_name"] == "Иван Иванов"
    assert item["deactivated_at"]
    # И причина, по которой её когда-то завели, тоже на месте.
    assert item["reason"]


def test_an_erroneous_mark_can_be_undone_and_the_trace_stays(client):
    buyer = _auth(client, "ivanov")
    created = _mark(client, buyer, url="https://restore-demo.example/x").json()
    client.delete(f"/intermediaries/{created['id']}", headers=buyer)

    restored = client.post(
        f"/intermediaries/{created['id']}/restore", headers=buyer
    )
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert restored.json()["deactivated_at"] is None
    # Причина и автор отметки от отмены не страдают.
    assert restored.json()["reason"]
    assert restored.json()["added_by_name"] == "Иван Иванов"


def test_auditor_cannot_undo(client):
    buyer = _auth(client, "ivanov")
    created = _mark(client, buyer, url="https://rbac-restore.example/x").json()
    auditor = _auth(client, "auditor")
    assert client.delete(f"/intermediaries/{created['id']}", headers=auditor).status_code == 403
    assert (
        client.post(f"/intermediaries/{created['id']}/restore", headers=auditor).status_code
        == 403
    )


def test_deactivated_rule_stops_affecting_new_searches(client):
    """Отключённая запись остаётся в истории, но выдачу больше не режет."""
    from app.core.db import SessionLocal
    from app.services.intermediaries import active_domains

    buyer = _auth(client, "ivanov")
    created = _mark(client, buyer, url="https://stops-demo.example/x").json()

    with SessionLocal() as db:
        assert "stops-demo.example" in active_domains(db)

    client.delete(f"/intermediaries/{created['id']}", headers=buyer)

    with SessionLocal() as db:
        assert "stops-demo.example" not in active_domains(db)


def test_a_url_without_a_domain_is_refused(client):
    buyer = _auth(client, "ivanov")
    response = client.post(
        "/intermediaries/mark",
        headers=buyer,
        json={"url": "localhost", "reason": "неважно"},
    )
    assert response.status_code == 422


def test_marking_a_subdomain_updates_the_covering_rule(client):
    """Поддомен площадки уже покрыт её правилом — второй записи не нужно.

    Отсев сравнивает домены по метке перед зоной, поэтому «21food.cn» уже
    ловит «wap.21food.cn». Отметка поддомена отдельной записью дублировала
    бы правило, ничего не меняя в выдаче.
    """
    buyer = _auth(client, "ivanov")
    base = _mark(client, buyer, url="https://cover-demo.example/a", name="Cover").json()

    sub = _mark(
        client,
        buyer,
        url="https://wap.cover-demo.example/b",
        reason="Мобильная версия той же площадки",
    ).json()

    assert sub["id"] == base["id"], "поддомен обязан попасть в то же правило"
    assert sub["domain"] == "cover-demo.example"
    assert sub["reason"] == "Мобильная версия той же площадки"

    listed = client.get("/intermediaries", headers=buyer).json()
    labels = [item["domain"] for item in listed if "cover-demo" in item["domain"]]
    assert labels == ["cover-demo.example"], labels
