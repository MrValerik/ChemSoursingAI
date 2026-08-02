"""Реестр посредников и отсев площадок до загрузки страниц."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_intermediaries.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Intermediary
from app.services.intermediaries import (
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
    """Совпадение по суффиксу не должно ловить чужой домен со схожим концом."""
    assert not is_intermediary("https://notechemi.com/p", {"echemi.com"})
    assert not is_intermediary("https://echemi.com.evil.ru/p", {"echemi.com"})


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


def test_registry_is_readable_by_a_buyer_and_editable_by_a_head(client):
    buyer = _auth(client, "ivanov")
    head = _auth(client, "petrova")

    listed = client.get("/intermediaries", headers=buyer)
    assert listed.status_code == 200
    assert any(item["domain"] == "echemi.com" for item in listed.json())

    denied = client.post(
        "/intermediaries",
        headers=buyer,
        json={"domain": "newshop.example", "name": "Новая площадка"},
    )
    assert denied.status_code == 403

    created = client.post(
        "/intermediaries",
        headers=head,
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
        headers=head,
        json={"domain": "newshop.example", "name": "Она же"},
    )
    assert duplicate.status_code == 409

    item_id = created.json()["id"]
    patched = client.patch(
        f"/intermediaries/{item_id}", headers=head, json={"is_active": False}
    )
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    assert client.delete(f"/intermediaries/{item_id}", headers=buyer).status_code == 403
    assert client.delete(f"/intermediaries/{item_id}", headers=head).status_code == 204


def test_unknown_kind_is_rejected(client):
    head = _auth(client, "petrova")
    response = client.post(
        "/intermediaries",
        headers=head,
        json={"domain": "x.example", "name": "X", "kind": "выдуманный"},
    )
    assert response.status_code == 422
