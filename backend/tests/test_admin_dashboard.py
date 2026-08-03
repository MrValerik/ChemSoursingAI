"""Тесты шага 6: администрирование пользователей, каналы, интеграции."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import IntegrationSetting


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_admin.db"):
        os.remove("test_admin.db")
    with TestClient(app) as c:
        yield c
    engine.dispose()
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


def test_channels_status_admin_only(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.settings.LLMClient.check_health", lambda self: (True, None)
    )
    admin = _login(client)
    channels = client.get("/settings/channels", headers=admin).json()
    names = {c["channel"] for c in channels}
    assert {"email", "whatsapp", "llm"} <= names
    email = next(c for c in channels if c["channel"] == "email")
    assert email["configured"] is False  # .env пуст в тестах
    llm = next(c for c in channels if c["channel"] == "llm")
    assert llm["configured"] is True

    assert (
        client.get("/settings/channels", headers=_login(client, "ivanov")).status_code
        == 403
    )


def test_llm_health_reports_availability(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.health.LLMClient.check_health", lambda self: (True, None)
    )
    response = client.get("/health/llm")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    monkeypatch.setattr(
        "app.api.health.LLMClient.check_health",
        lambda self: (False, "connection refused"),
    )
    response = client.get("/health/llm")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert "connection refused" not in response.text


def test_integration_settings_encrypt_secrets_and_require_admin(client, monkeypatch):
    admin = _login(client)
    email_payload = {
        "enabled": True,
        "delivery_mode": "live",
        "email_from": "tester@example.com",
        "email_from_name": "ChemSource Test",
        "auto_followup_mode": "draft",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_user": "tester@example.com",
        "smtp_password": "smtp-secret",
        "smtp_use_ssl": True,
        "smtp_starttls": False,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_user": "tester@example.com",
        "imap_password": "imap-secret",
        "imap_use_ssl": True,
        "imap_folder": "INBOX",
    }
    response = client.put(
        "/settings/integrations/email",
        json=email_payload,
        headers=admin,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["smtp_password_set"] is True
    assert "smtp-secret" not in response.text
    assert "imap-secret" not in response.text
    with SessionLocal() as db:
        stored = db.query(IntegrationSetting).filter_by(channel="email").one()
        assert "smtp-secret" not in stored.encrypted_config
        assert "imap-secret" not in stored.encrypted_config

    response = client.put(
        "/settings/integrations/whatsapp",
        json={
            "enabled": True,
            "phone_id": "123456789",
            "access_token": "whatsapp-secret",
            "api_base_url": "https://graph.facebook.com",
            "api_version": "v23.0",
        },
        headers=admin,
    )
    assert response.status_code == 200
    assert response.json()["token_set"] is True
    assert "whatsapp-secret" not in response.text

    monkeypatch.setattr(
        "app.api.settings.EmailConnector.check_connections",
        lambda self: {"smtp": True, "imap": True},
    )
    assert (
        client.post("/settings/integrations/email/check", headers=admin).status_code
        == 200
    )
    monkeypatch.setattr(
        "app.api.settings.WhatsAppConnector.check_health",
        lambda self: {"verified_name": "Test", "display_phone_number": "+100"},
    )
    assert (
        client.post(
            "/settings/integrations/whatsapp/check", headers=admin
        ).status_code
        == 200
    )

    buyer = _login(client, "ivanov")
    assert (
        client.get("/settings/integrations/email", headers=buyer).status_code
        == 403
    )
    assert client.get("/communication-testing", headers=buyer).status_code == 403


def test_communication_testing_preview_and_explicit_delivery(
    client, monkeypatch
):
    admin = _login(client)
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: "Спасибо. Пожалуйста, пришлите CoA и TDS.",
    )
    preview = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "customer_message": "We can offer the material.",
            "reply_language": "ru",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert preview.status_code == 201
    assert preview.json()["status"] == "previewed"
    assert preview.json()["recipient_masked"] == "ow***@example.com"

    not_confirmed = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "customer_message": "Test",
            "delivery_mode": "send",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert not_confirmed.status_code == 422

    monkeypatch.setattr(
        "app.services.communication_testing.EmailConnector.send",
        lambda self, **kwargs: "<test-message@example.com>",
    )
    sent_email = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "customer_message": "Test",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert sent_email.status_code == 201
    assert sent_email.json()["status"] == "sent"

    monkeypatch.setattr(
        "app.services.communication_testing.WhatsAppConnector.send_text",
        lambda self, **kwargs: "wamid.test",
    )
    sent_whatsapp = client.post(
        "/communication-testing",
        json={
            "channel": "whatsapp",
            "recipient": "+79000000000",
            "customer_message": "Test",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert sent_whatsapp.status_code == 201
    assert sent_whatsapp.json()["provider_message_id"] == "wamid.test"

    history = client.get("/communication-testing", headers=admin)
    assert history.status_code == 200
    assert len(history.json()) >= 3
