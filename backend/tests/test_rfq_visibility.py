"""Тесты ролевой видимости RFQ (шаг 2 UI/UX-плана).

Закупщик видит только свои запросы, руководитель/аудитор — все,
аудитор не может создавать. Без внешних сервисов (verify=false).
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_visibility.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models import RFQ, SearchRun


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


def test_owner_can_delete_request_and_active_search_is_cancelled(client):
    ivanov = _login(client, "ivanov")
    created = client.post(
        "/rfq?verify=false&start_search=true",
        json={
            "cas": "64-17-5",
            "name": "Ethanol to delete",
            "incoterms": ["CIP"],
            "search_countries": ["Индия"],
        },
        headers=ivanov,
    )
    assert created.status_code == 201
    rfq_id = created.json()["id"]

    response = client.delete(f"/rfq/{rfq_id}", headers=ivanov)

    assert response.status_code == 204
    assert client.delete(f"/rfq/{rfq_id}", headers=ivanov).status_code == 204
    missing_response = client.get(f"/rfq/{rfq_id}", headers=ivanov)
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Запрос не найден"
    assert rfq_id not in {
        item["id"] for item in client.get("/rfq", headers=ivanov).json()
    }
    assert (
        client.post(
            f"/supplier-search/jobs?rfq_id={rfq_id}",
            headers=ivanov,
            json={
                "cas": "64-17-5",
                "name": "Ethanol",
                "country": "Индия",
            },
        ).status_code
        == 404
    )

    with SessionLocal() as db:
        rfq = db.get(RFQ, rfq_id)
        search_run = db.query(SearchRun).filter(SearchRun.rfq_id == rfq_id).one()
        assert rfq.deleted_at is not None
        assert rfq.deleted_by_id is not None
        assert search_run.status == "cancelled"
        assert search_run.completed_at is not None


def test_delete_request_respects_role_boundaries(client):
    head = _login(client, "petrova")
    created = _create_rfq(client, head)
    assert created.status_code == 201
    rfq_id = created.json()["id"]

    assert (
        client.delete(f"/rfq/{rfq_id}", headers=_login(client, "ivanov")).status_code
        == 404
    )
    assert (
        client.delete(f"/rfq/{rfq_id}", headers=_login(client, "auditor")).status_code
        == 403
    )
    assert client.delete(f"/rfq/{rfq_id}").status_code == 401
    assert (
        client.delete(f"/rfq/{rfq_id}", headers=_login(client, "admin")).status_code
        == 204
    )
