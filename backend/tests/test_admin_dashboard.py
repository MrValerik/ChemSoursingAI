"""Тесты шага 6: администрирование пользователей, каналы, интеграции."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.connectors.pubchem import SubstanceInfo
from app.core.db import SessionLocal, engine
from app.extraction.llm_client import LLMUnavailableError
from app.main import app
from app.models import CommunicationTestRun, IntegrationSetting
from app.services.communication_policy import CommunicationPolicyDecision


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


def test_whatsapp_pairing_code_is_admin_only_and_not_cached(client, monkeypatch):
    admin = _login(client)

    class FakeWebConnector:
        def web_pairing_code(self, phone_number: str) -> dict:
            assert phone_number == "79000000000"
            return {"pairing_code": "ABCD1234", "expires_in_seconds": 180}

    monkeypatch.setattr(
        "app.api.settings._web_connector", lambda _db: FakeWebConnector()
    )
    response = client.post(
        "/settings/integrations/whatsapp/web/pairing-code",
        json={"phone_number": "+7 (900) 000-00-00"},
        headers=admin,
    )
    assert response.status_code == 200
    assert response.json() == {
        "pairing_code": "ABCD1234",
        "expires_in_seconds": 180,
    }
    assert response.headers["cache-control"] == "no-store"

    buyer = _login(client, "ivanov")
    assert (
        client.post(
            "/settings/integrations/whatsapp/web/pairing-code",
            json={"phone_number": "+7 (900) 000-00-00"},
            headers=buyer,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/settings/integrations/whatsapp/web/pairing-code",
            json={"phone_number": "123"},
            headers=admin,
        ).status_code
        == 422
    )


def test_communication_testing_preview_and_explicit_delivery(
    client, monkeypatch
):
    admin = _login(client)
    llm_calls = []

    def fake_generate_text(self, **kwargs):
        llm_calls.append(kwargs)
        if "переводчик переписки" in kwargs["system_prompt"]:
            return (
                "Здравствуйте. Нам требуется 50 кг аммиака. "
                "Сообщите цену и предоставьте CoA."
            )
        if "История диалога" in kwargs["user_text"]:
            return (
                "**Thank you.** Please also confirm the lead time and Incoterms. "
                "This is a test message."
            )
        return (
            "**Subject: Request**\n**Hello.** We need 50 kg of ammonia.\n"
            "* Please quote and provide a `CoA`.\n"
            "This message was generated for testing purposes."
        )

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        fake_generate_text,
    )
    monkeypatch.setattr(
        "app.services.communication_testing.classify_supplier_message",
        lambda *args, **kwargs: CommunicationPolicyDecision(
            auto_reply_allowed=True,
            category="standard_procurement",
            explanation="Обычный ответ по закупке.",
            method="test",
        ),
    )
    preview = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": "50 кг аммиака, нужны цена и CoA",
            "reply_language": "en",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert preview.status_code == 201
    assert preview.json()["status"] == "previewed"
    assert preview.json()["recipient_masked"] == "не задан"
    assert preview.json()["procurement_context"] == "50 кг аммиака, нужны цена и CoA"
    assert preview.json()["generated_reply"] == (
        "Hello. We need 50 kg of ammonia.\n"
        "Please quote and provide a CoA."
    )
    assert "*" not in preview.json()["generated_reply"]
    assert [message["sender_role"] for message in preview.json()["messages"]] == [
        "assistant"
    ]
    assert preview.json()["messages"][0]["translation_ru"].startswith("Здравствуйте")
    dialogue_calls = [
        item for item in llm_calls if "переводчик переписки" not in item["system_prompt"]
    ]
    assert "первое сообщение" in dialogue_calls[0]["user_text"]
    assert "лабораторный образец" in dialogue_calls[0]["system_prompt"]
    assert "Канал — Email" in dialogue_calls[0]["additional_instructions"]
    assert "первый контакт" in dialogue_calls[0]["additional_instructions"]

    continued = client.post(
        f"/communication-testing/{preview.json()['id']}/messages",
        json={
            "supplier_message": "USD 700 per ton, MOQ 100 kg.",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert continued.status_code == 201
    assert "*" not in continued.json()["generated_reply"]
    assert [message["sender_role"] for message in continued.json()["messages"]] == [
        "assistant",
        "supplier",
        "assistant",
    ]
    dialogue_calls = [
        item for item in llm_calls if "переводчик переписки" not in item["system_prompt"]
    ]
    assert "50 кг аммиака" in dialogue_calls[1]["user_text"]
    assert "ПОСТАВЩИК_НЕДОВЕРЕННЫЙ" in dialogue_calls[1]["user_text"]
    assert "USD 700 per ton" in dialogue_calls[1]["user_text"]
    assert "продолжение диалога" in dialogue_calls[1]["additional_instructions"]
    assert all(message["translation_ru"] for message in continued.json()["messages"])

    buyer = _login(client, "ivanov")
    assert (
        client.post(
            f"/communication-testing/{preview.json()['id']}/messages",
            json={"supplier_message": "Test"},
            headers=buyer,
        ).status_code
        == 403
    )

    not_confirmed = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "procurement_context": "Test",
            "delivery_mode": "send",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert not_confirmed.status_code == 422

    provider_ids = iter(
        ("<test-message@example.com>", "<test-followup@example.com>")
    )
    monkeypatch.setattr(
        "app.services.communication_testing.EmailConnector.send",
        lambda self, **kwargs: next(provider_ids),
    )
    sent_email = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "procurement_context": "Test",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert sent_email.status_code == 201
    assert sent_email.json()["status"] == "sent"

    followup_not_confirmed = client.post(
        f"/communication-testing/{sent_email.json()['id']}/messages",
        json={
            "supplier_message": "Our lead time is two weeks.",
            "recipient": "owner@example.com",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert followup_not_confirmed.status_code == 422

    sent_followup = client.post(
        f"/communication-testing/{sent_email.json()['id']}/messages",
        json={
            "supplier_message": "Our lead time is two weeks.",
            "recipient": "owner@example.com",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert sent_followup.status_code == 201
    assert sent_followup.json()["status"] == "sent"
    assert [
        message["sender_role"] for message in sent_followup.json()["messages"]
    ] == ["assistant", "supplier", "assistant"]

    monkeypatch.setattr(
        "app.services.communication_testing.WhatsAppConnector.send_text",
        lambda self, **kwargs: "wamid.test",
    )
    sent_whatsapp = client.post(
        "/communication-testing",
        json={
            "channel": "whatsapp",
            "recipient": "+79000000000",
            "procurement_context": "Test",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert sent_whatsapp.status_code == 201
    assert sent_whatsapp.json()["provider_message_id"] == "wamid.test"
    dialogue_calls = [
        item for item in llm_calls if "переводчик переписки" not in item["system_prompt"]
    ]
    assert "Канал — WhatsApp" in dialogue_calls[-1]["additional_instructions"]

    history = client.get("/communication-testing", headers=admin)
    assert history.status_code == 200
    assert len(history.json()) >= 3
    assert history.json()[0]["messages"][0]["sender_role"] == "assistant"


def test_communication_testing_escalates_social_reply_without_generation(
    client, monkeypatch
):
    admin = _login(client)
    dialogue_calls = []

    def fake_generate_text(self, **kwargs):
        if "переводчик переписки" in kwargs["system_prompt"]:
            return "Как у вас дела сегодня?"
        dialogue_calls.append(kwargs)
        return "Hello. We need 50 kg of ammonia. Please provide your quote."

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        fake_generate_text,
    )

    preview = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": "50 кг аммиака",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert preview.status_code == 201

    continued = client.post(
        f"/communication-testing/{preview.json()['id']}/messages",
        json={
            "supplier_message": "Before we proceed, how are you today?",
            "confirm_external_send": False,
        },
        headers=admin,
    )

    assert continued.status_code == 201
    assert continued.json()["status"] == "escalated"
    assert "Требуется ответ человека" in continued.json()["error"]
    assert "social_or_personal" in continued.json()["error"]
    assert [message["sender_role"] for message in continued.json()["messages"]] == [
        "assistant",
        "supplier",
    ]
    assert len(dialogue_calls) == 1


def test_communication_testing_preserves_reply_when_classifier_is_unavailable(
    client, monkeypatch
):
    admin = _login(client)

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: (
            "Здравствуйте. Запросите цену."
            if "переводчик переписки" in kwargs["system_prompt"]
            else "Hello. Please provide your price."
        ),
    )
    preview = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": "50 кг аммиака",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert preview.status_code == 201

    monkeypatch.setattr(
        "app.services.communication_testing._communication_test_llm_client",
        lambda: (_ for _ in ()).throw(LLMUnavailableError("offline")),
    )
    continued = client.post(
        f"/communication-testing/{preview.json()['id']}/messages",
        json={
            "supplier_message": "The price is USD 12/kg.",
            "confirm_external_send": False,
        },
        headers=admin,
    )

    assert continued.status_code == 201
    assert continued.json()["status"] == "escalated"
    assert "Категория: unclear" in continued.json()["error"]
    assert [message["sender_role"] for message in continued.json()["messages"]] == [
        "assistant",
        "supplier",
    ]
    assert continued.json()["messages"][-1]["content"] == "The price is USD 12/kg."
    assert continued.json()["messages"][-1]["translation_ru"] is None


def test_communication_testing_stops_before_first_message_on_identity_conflict(
    client, monkeypatch
):
    admin = _login(client)

    monkeypatch.setattr(
        "app.services.communication_testing.PubChemConnector.verify_cas",
        lambda self, cas: SubstanceInfo(
            cas=cas,
            found=True,
            iupac_name="propan-2-one",
            synonyms=["Acetone", "2-Propanone"],
        ),
    )
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_json",
        lambda self, **kwargs: {
            "route": "escalate",
            "category": "conflict",
            "explanation": "Метанол не соответствует CAS ацетона.",
        },
    )
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("RFQ must not be generated")
        ),
    )

    response = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": (
                "100 кг метанола, CAS 67-64-1, чистота 99,9%. Нужна цена."
            ),
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "escalated"
    assert response.json()["messages"] == []
    assert response.json()["generated_reply"] is None
    assert "Метанол не соответствует CAS ацетона" in response.json()["error"]
    assert "identity_or_custom_synthesis" in response.json()["error"]


def test_communication_testing_regenerates_non_english_reply_and_translates_it(
    client, monkeypatch
):
    admin = _login(client)
    generated = iter(
        (
            "Здравствуйте. Сообщите, пожалуйста, цену и срок поставки.",
            "Hello. Please provide your price and lead time.",
            "Здравствуйте. Сообщите, пожалуйста, цену и срок поставки.",
        )
    )
    llm_calls = []

    def fake_generate_text(self, **kwargs):
        llm_calls.append(kwargs)
        return next(generated)

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        fake_generate_text,
    )

    response = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": "50 кг аммиака",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )

    assert response.status_code == 201
    assert response.json()["generated_reply"].startswith("Hello")
    assert response.json()["reply_language"] == "en"
    assert response.json()["messages"][0]["translation_ru"].startswith("Здравствуйте")
    assert len(llm_calls) == 3
    assert "REQUIRED LANGUAGE" in llm_calls[0]["additional_instructions"]
    assert "предыдущая попытка" in llm_calls[1]["additional_instructions"]
    assert "строго соблюдай язык: английском" in llm_calls[1]["additional_instructions"]
    assert "ВНУТРЕННИЙ ПЕРЕВОД" in llm_calls[2]["additional_instructions"]

    rejected_language = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "",
            "procurement_context": "50 кг аммиака",
            "reply_language": "ru",
            "delivery_mode": "preview",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert rejected_language.status_code == 422


def test_communication_testing_stops_send_after_two_non_english_replies(
    client, monkeypatch
):
    admin = _login(client)
    smtp_calls = []

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: "Здравствуйте. Сообщите цену и срок поставки.",
    )
    monkeypatch.setattr(
        "app.services.communication_testing.EmailConnector.send",
        lambda self, **kwargs: smtp_calls.append(kwargs) or "unexpected",
    )

    response = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "owner@example.com",
            "procurement_context": "50 кг аммиака",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )

    assert response.status_code == 503
    assert "дважды вернула сообщение не на выбранном английском языке" in response.json()[
        "detail"
    ]
    assert smtp_calls == []
    with SessionLocal() as db:
        saved = db.scalar(
            select(CommunicationTestRun).order_by(CommunicationTestRun.id.desc())
        )
        assert saved is not None
        assert saved.status == "llm_error"
        assert saved.generated_reply is None
        assert saved.error is not None
        assert "Отправка остановлена" in saved.error
