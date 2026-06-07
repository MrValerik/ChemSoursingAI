"""Тесты шага 3: ручная эскалация и история закупочных цен по CAS."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_card.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_card.db"):
        os.remove("test_card.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_card.db"):
        os.remove("test_card.db")


def _login(client, username="ivanov"):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_manual_escalation(client):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=headers,
    ).json()

    resp = client.post(
        f"/rfq/{rfq['id']}/escalate",
        json={"reason": "logistics", "note": "Опасный груз"},
        headers=headers,
    )
    assert resp.status_code == 201
    esc = resp.json()
    assert esc["reason"] == "logistics"
    assert esc["status"] == "open"

    # Статус RFQ переведён в «Ручной разбор».
    updated = client.get(f"/rfq/{rfq['id']}", headers=headers).json()
    assert updated["status"] == "escalated"

    # Аудитор не может эскалировать.
    auditor = _login(client, "auditor")
    resp = client.post(
        f"/rfq/{rfq['id']}/escalate", json={"reason": "other"}, headers=auditor
    )
    assert resp.status_code == 403


def test_price_history(client):
    headers = _login(client)
    # Два запроса по одному CAS: котировка первого видна как история для второго.
    rfq1 = client.post(
        "/rfq?verify=false",
        json={"cas": "56-40-6", "name": "Glycine", "incoterms": ["FCA"]},
        headers=headers,
    ).json()
    client.post(
        "/quotations",
        json={"rfq_id": rfq1["id"], "price": 5.5, "currency": "USD", "incoterm": "FCA"},
        headers=headers,
    )

    resp = client.get("/substances/price-history?cas=56-40-6", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["price"] == 5.5
    assert items[0]["rfq_id"] == rfq1["id"]

    # По другому CAS — пусто.
    assert client.get(
        "/substances/price-history?cas=50-78-2", headers=headers
    ).json() == []
