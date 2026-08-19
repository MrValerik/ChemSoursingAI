"""Список поставщиков не должен падать из-за запроса без CAS.

Половина списка заказчика — торговые марки и смеси без номера, и поиск
давно умеет работать по названию. Схема связи «поставщик — запрос» при
этом требовала CAS строкой, и весь GET /suppliers отвечал 500, стоило
компании оказаться связанной с таким запросом. На стенде это 22 связи с
запросом «Dowsil 556»: вкладка «Отобранные компании» не открывалась.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_suppliers_no_cas.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, RfqSupplierLink, Supplier, User


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_suppliers_no_cas.db"):
        os.remove("test_suppliers_no_cas.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_suppliers_no_cas.db"):
        os.remove("test_suppliers_no_cas.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_a_supplier_linked_to_a_request_without_cas_is_listed(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas=None, name="Dowsil 556 Cosmetic Grade Fluid", owner_id=owner.id)
        supplier = Supplier(company="Топсиликон", source="https://topsilicone.example")
        db.add_all([rfq, supplier])
        db.flush()
        db.add(
            RfqSupplierLink(
                rfq_id=rfq.id,
                supplier_id=supplier.id,
                source_url="https://topsilicone.example",
                status="candidate",
            )
        )
        db.commit()
        supplier_id = supplier.id

    response = client.get("/suppliers", headers=_auth(client, "ivanov"))

    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json()}
    assert supplier_id in listed
    link = listed[supplier_id]["linked_requests"][0]
    assert link["name"] == "Dowsil 556 Cosmetic Grade Fluid"
    assert link["cas"] is None
