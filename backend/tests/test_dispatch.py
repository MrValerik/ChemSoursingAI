"""Тесты шага 4: поставщики, выбор получателей, рассылка со статусами."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dispatch.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_dispatch.db"):
        os.remove("test_dispatch.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_dispatch.db"):
        os.remove("test_dispatch.db")


def _login(client, username="ivanov"):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_supplier_registry_seeded(client):
    headers = _login(client)
    suppliers = client.get("/suppliers", headers=headers).json()
    assert len(suppliers) >= 3
    haihua = next(s for s in suppliers if s["company"] == "Shandong Haihua")
    assert "email" in haihua["channels"]


def test_add_supplier_manually(client):
    headers = _login(client)
    resp = client.post(
        "/suppliers",
        json={"company": "Test Chem GmbH", "email": "sales@test.example"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "добавлен вручную"
    assert resp.json()["channels"] == ["email"]


def test_select_and_dispatch(client):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    suppliers = client.get("/suppliers", headers=headers).json()
    s1, s2 = suppliers[0], suppliers[1]

    # Выбор получателей (идемпотентный).
    payload = {
        "items": [
            {"supplier_id": s1["id"], "channel": "email"},
            {"supplier_id": s2["id"], "channel": "email"},
        ]
    }
    recipients = client.post(
        f"/rfq/{rfq['id']}/recipients", json=payload, headers=headers
    ).json()
    assert len(recipients) == 2
    assert all(r["status"] == "queued" for r in recipients)
    recipients = client.post(
        f"/rfq/{rfq['id']}/recipients", json=payload, headers=headers
    ).json()
    assert len(recipients) == 2  # повтор не дублирует

    # Отмена одного, пока в очереди.
    resp = client.delete(
        f"/rfq/{rfq['id']}/recipients/{recipients[1]['id']}", headers=headers
    )
    assert resp.status_code == 204

    # Рассылка: queued -> sent, статус RFQ -> sent.
    sent = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers).json()
    assert len(sent) == 1
    assert sent[0]["status"] == "sent"
    updated = client.get(f"/rfq/{rfq['id']}", headers=headers).json()
    assert updated["status"] == "sent"

    # После отправки отмена недоступна, повторная рассылка — 422 (очередь пуста).
    resp = client.delete(
        f"/rfq/{rfq['id']}/recipients/{sent[0]['id']}", headers=headers
    )
    assert resp.status_code == 422
    assert client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers).status_code == 422
