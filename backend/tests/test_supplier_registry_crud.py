"""Ручное ведение строк глобального реестра поставщиков по ролям."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_supplier_registry_crud.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_supplier_registry_crud.db"):
        os.remove("test_supplier_registry_crud.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_supplier_registry_crud.db"):
        os.remove("test_supplier_registry_crud.db")


def _auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_buyer_can_create_update_and_delete_an_unused_supplier(client):
    buyer = _auth(client, "ivanov")
    created = client.post(
        "/suppliers",
        headers=buyer,
        json={
            "company": "Тест Хим",
            "type": "distributor",
            "country": "Россия",
            "source": "добавлен вручную",
            "email": "sales@test-chem.example",
        },
    )
    assert created.status_code == 201
    supplier_id = created.json()["id"]

    updated = client.patch(
        f"/suppliers/{supplier_id}",
        headers=buyer,
        json={
            "company": "Тест Хим Производство",
            "type": "manufacturer",
            "country": "Китай",
            "source": "https://test-chem.example",
            "reputation": "Проверить документы",
            "qualification_status": "under_review",
            "evidence_score": 72,
            "certificates": ["ISO 9001", "GMP", "ISO 9001"],
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["company"] == "Тест Хим Производство"
    assert body["type"] == "manufacturer"
    assert body["country"] == "Китай"
    assert body["qualification_status"] == "under_review"
    assert body["evidence_score"] == 72
    assert body["certificates"] == ["ISO 9001", "GMP"]
    assert body["last_checked_at"] is not None
    assert body["verified_by_name"] is None

    verified = client.patch(
        f"/suppliers/{supplier_id}",
        headers=buyer,
        json={"qualification_status": "verified"},
    )
    assert verified.status_code == 200
    assert verified.json()["verified_by_name"] == "Иван Иванов"

    listed_verified = client.get("/suppliers", headers=buyer).json()
    registry_item = next(item for item in listed_verified if item["id"] == supplier_id)
    assert registry_item["verified_by_name"] == "Иван Иванов"

    auditor = _auth(client, "auditor")
    assert (
        client.post(
            "/suppliers",
            headers=auditor,
            json={"company": "Недопустимое добавление"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/suppliers/{supplier_id}",
            headers=auditor,
            json={"country": "Индия"},
        ).status_code
        == 403
    )
    assert client.delete(f"/suppliers/{supplier_id}", headers=auditor).status_code == 403

    deleted = client.delete(f"/suppliers/{supplier_id}", headers=buyer)
    assert deleted.status_code == 204
    listed = client.get("/suppliers", headers=buyer)
    assert listed.status_code == 200
    assert all(item["id"] != supplier_id for item in listed.json())


def test_supplier_linked_to_a_request_cannot_lose_its_history(client):
    buyer = _auth(client, "ivanov")
    rfq = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={
            "cas": "64-17-5",
            "name": "Этанол",
            "incoterms": ["CIP"],
        },
    )
    assert rfq.status_code == 201
    created = client.post(
        f"/suppliers?rfq_id={rfq.json()['id']}",
        headers=buyer,
        json={"company": "Связанный поставщик", "country": "Индия"},
    )
    assert created.status_code == 201

    denied = client.delete(f"/suppliers/{created.json()['id']}", headers=buyer)
    assert denied.status_code == 409
    assert "сохранить историю" in denied.json()["detail"]
