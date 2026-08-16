"""Контакт, вписанный закупщиком с сайта компании.

Поиск читает страницу машиной и останавливается на трёх преградах:
адрес подменён заглушкой от спам-ботов, вместо адреса форма обратной
связи, компанию назвала площадка и своей страницы у нас нет. В таблице
такая компания стоит с пометкой «связь через площадку» и без галочки:
канал берётся из контактов, а контактов нет.

Человек эти преграды проходит — открывает сайт и видит адрес глазами.
Дальше ему нужно место, куда этот адрес положить.

Убрать контакт так же важно, как добавить: адрес вводят руками, а письмо
по ошибочному адресу уходит постороннему человеку.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_manual_contact.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, RfqSupplierLink, Supplier, User


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_manual_contact.db"):
        os.remove("test_manual_contact.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_manual_contact.db"):
        os.remove("test_manual_contact.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def unreachable(client):
    """Компания, найденная площадкой: канала связи у неё нет."""
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas="50-78-2", name="Ацетилсалициловая кислота", owner_id=owner.id)
        supplier = Supplier(
            company="Hebei Chuanghai Biotechnology Co., Ltd",
            source="https://chuanghai11.en.made-in-china.com/",
            contact_barrier="platform",
        )
        db.add_all([rfq, supplier])
        db.flush()
        db.add(RfqSupplierLink(rfq_id=rfq.id, supplier_id=supplier.id))
        db.commit()
        return {"rfq_id": rfq.id, "supplier_id": supplier.id}


def test_a_typed_contact_gives_the_company_a_channel(client, unreachable):
    """Без канала галочку рассылки поставить нельзя — в этом весь смысл."""
    response = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"full_name": "Отдел продаж", "email": "sales@chuanghai.com"},
        params={"rfq_id": unreachable["rfq_id"]},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["channels"] == ["email"]
    assert body["contacts"][0]["email"] == "sales@chuanghai.com"
    assert body["contacts"][0]["full_name"] == "Отдел продаж"


def test_the_barrier_survives_a_typed_contact(client, unreachable):
    """Преграда — про наши источники, а не про наличие адреса у компании.

    В таблице она показывается только там, где канала нет, поэтому живому
    адресу не мешает. Зато если вписанный контакт потом уберут, закупщик
    снова прочтёт, почему машина связи не нашла, а не голое «нет контакта».
    """
    headers = _auth(client, "ivanov")
    added = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "sales@chuanghai.com"},
        headers=headers,
    ).json()

    assert added["channels"] == ["email"]
    assert added["contact_barrier"] == "platform"

    removed = client.delete(
        f"/suppliers/{unreachable['supplier_id']}/contacts/"
        f"{added['contacts'][0]['id']}",
        headers=headers,
    ).json()

    assert removed["channels"] == []
    assert removed["contact_barrier"] == "platform"


def test_the_contact_remembers_which_substance_it_was_added_for(
    client, unreachable
):
    response = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"whatsapp": "+86 311 000 00 00"},
        params={"rfq_id": unreachable["rfq_id"]},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 201
    contact = response.json()["contacts"][0]
    assert contact["offered_substances"] == ["Ацетилсалициловая кислота"]


def test_a_name_alone_is_not_a_channel(client, unreachable):
    """Компания с именем без адреса так и осталась бы недостижимой."""
    response = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"full_name": "Ли Вэй"},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Нужен адрес почты или номер WhatsApp"


def test_a_mistyped_address_is_refused(client, unreachable):
    """Адрес переносят руками, глядя на сайт, — опечатка вероятна."""
    response = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "sales@chuanghai"},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Адрес почты введён с ошибкой"


def test_the_same_address_is_not_added_twice(client, unreachable):
    headers = _auth(client, "ivanov")
    first = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "sales@chuanghai.com"},
        headers=headers,
    )
    second = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "SALES@chuanghai.com"},
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 409


def test_a_wrong_address_can_be_removed(client, unreachable):
    headers = _auth(client, "ivanov")
    added = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "slaes@chuanghai.com"},
        headers=headers,
    ).json()
    contact_id = added["contacts"][0]["id"]

    response = client.delete(
        f"/suppliers/{unreachable['supplier_id']}/contacts/{contact_id}",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["contacts"] == []
    assert response.json()["channels"] == []


def test_a_contact_of_another_company_is_not_touched(client, unreachable):
    """Номер контакта приходит из браузера, и он не должен открывать чужую."""
    with SessionLocal() as db:
        other = Supplier(company="Посторонняя компания")
        db.add(other)
        db.flush()
        manager = Manager(supplier_id=other.id, email="someone@example.org")
        db.add(manager)
        db.commit()
        foreign_contact_id = manager.id

    response = client.delete(
        f"/suppliers/{unreachable['supplier_id']}/contacts/{foreign_contact_id}",
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 404
    with SessionLocal() as db:
        assert db.get(Manager, foreign_contact_id) is not None


def test_the_auditor_only_reads(client, unreachable):
    response = client.post(
        f"/suppliers/{unreachable['supplier_id']}/contacts",
        json={"email": "sales@chuanghai.com"},
        headers=_auth(client, "auditor"),
    )

    assert response.status_code == 403
