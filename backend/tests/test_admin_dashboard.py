"""Тесты шага 6: администрирование пользователей, каналы, дашборд."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_admin.db"):
        os.remove("test_admin.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_admin.db"):
        os.remove("test_admin.db")


def _login(client, username="admin"):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_user_administration(client):
    admin = _login(client)

    # Создание пользователя.
    resp = client.post(
        "/users",
        json={
            "username": "sidorov",
            "full_name": "Пётр Сидоров",
            "password": "secret99",
            "role": "buyer",
        },
        headers=admin,
    )
    assert resp.status_code == 201
    uid = resp.json()["id"]

    # Дубль логина — конфликт.
    assert (
        client.post(
            "/users",
            json={
                "username": "sidorov",
                "full_name": "X",
                "password": "secret99",
            },
            headers=admin,
        ).status_code
        == 409
    )

    # Новый пользователь может войти.
    resp = client.post(
        "/auth/login", json={"username": "sidorov", "password": "secret99"}
    )
    assert resp.status_code == 200

    # Смена роли и отключение.
    resp = client.patch(f"/users/{uid}", json={"role": "head"}, headers=admin)
    assert resp.json()["role"] == "head"
    client.patch(f"/users/{uid}", json={"is_active": False}, headers=admin)
    assert (
        client.post(
            "/auth/login", json={"username": "sidorov", "password": "secret99"}
        ).status_code
        == 401
    )

    # Закупщику админка недоступна.
    buyer = _login(client, "ivanov")
    assert (
        client.post(
            "/users",
            json={"username": "x", "full_name": "X", "password": "secret99"},
            headers=buyer,
        ).status_code
        == 403
    )

    # Нельзя отключить себя.
    me = client.get("/auth/me", headers=admin).json()
    assert (
        client.patch(
            f"/users/{me['id']}", json={"is_active": False}, headers=admin
        ).status_code
        == 422
    )


def test_channels_status_admin_only(client):
    admin = _login(client)
    channels = client.get("/settings/channels", headers=admin).json()
    names = {c["channel"] for c in channels}
    assert {"email", "whatsapp", "llm"} <= names
    email = next(c for c in channels if c["channel"] == "email")
    assert email["configured"] is False  # .env пуст в тестах

    assert (
        client.get("/settings/channels", headers=_login(client, "ivanov")).status_code
        == 403
    )


def test_dashboard_role_adapted(client):
    buyer = _login(client, "ivanov")
    client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=buyer,
    )

    data = client.get("/dashboard", headers=buyer).json()
    assert data["role"] == "buyer"
    assert data["in_work"] >= 1
    assert "workload" not in data

    head = _login(client, "petrova")
    data = client.get("/dashboard", headers=head).json()
    assert data["role"] == "head"
    assert "workload" in data
    assert any(w["owner"] == "Иван Иванов" for w in data["workload"])
