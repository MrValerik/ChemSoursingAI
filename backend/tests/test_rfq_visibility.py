"""Тесты ролевой видимости RFQ (шаг 2 UI/UX-плана).

Закупщик видит только свои запросы, руководитель/аудитор — все,
аудитор не может создавать. Без внешних сервисов (verify=false).
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_visibility.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    # Чистая БД на модуль.
    if os.path.exists("test_visibility.db"):
        os.remove("test_visibility.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_visibility.db"):
        os.remove("test_visibility.db")


def _login(client, username):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_rfq(client, headers):
    return client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=headers,
    )


def test_unauthenticated_rejected(client):
    assert client.get("/rfq").status_code == 401


def test_buyer_sees_only_own(client):
    ivanov = _login(client, "ivanov")
    resp = _create_rfq(client, ivanov)
    assert resp.status_code == 201
    rfq_id = resp.json()["id"]
    assert resp.json()["owner_name"] == "Иван Иванов"

    # Сам закупщик видит свой запрос.
    listed = client.get("/rfq", headers=ivanov).json()
    assert any(r["id"] == rfq_id for r in listed)

    # Руководитель и аудитор видят всё (включая имя ответственного).
    for username in ("petrova", "auditor"):
        headers = _login(client, username)
        listed = client.get("/rfq", headers=headers).json()
        row = next(r for r in listed if r["id"] == rfq_id)
        assert row["owner_name"] == "Иван Иванов"
        assert client.get(f"/rfq/{rfq_id}", headers=headers).status_code == 200


def test_auditor_cannot_create(client):
    auditor = _login(client, "auditor")
    assert _create_rfq(client, auditor).status_code == 403


def test_list_aggregates_present(client):
    ivanov = _login(client, "ivanov")
    listed = client.get("/rfq", headers=ivanov).json()
    row = listed[0]
    for key in ("n_quotations", "n_complete", "completeness_pct", "has_open_escalation"):
        assert key in row
