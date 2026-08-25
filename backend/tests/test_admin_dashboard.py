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


@pytest.fixture(autouse=True)
def google_translate_stub(monkeypatch):
    """Диалоги в API-тестах переводятся без обращения к внешнему Google."""
    monkeypatch.setattr(
        "app.services.communication_testing.GoogleTranslateConnector.translate",
        lambda self, text, **kwargs: f"Здравствуйте. {text}",
    )


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
            "* Please confirm CAS, grade and form; quote and provide a `CoA`.\n"
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
        "Please confirm CAS, grade and form; quote and provide a CoA."
    )
    assert "*" not in preview.json()["generated_reply"]
    assert [message["sender_role"] for message in preview.json()["messages"]] == [
        "assistant"
    ]
    assert preview.json()["messages"][0]["translation_ru"] is None
    translated = client.post(
        f"/communication-testing/{preview.json()['id']}/translation",
        headers=admin,
    )
    assert translated.status_code == 200
    assert translated.json()["messages"][0]["translation_ru"].startswith("Здравствуйте")
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
    llm_call_count = len(llm_calls)
    translated_dialogue = client.post(
        f"/communication-testing/{preview.json()['id']}/translation",
        headers=admin,
    )
    assert translated_dialogue.status_code == 200
    assert all(
        message["translation_ru"].startswith("Здравствуйте")
        for message in translated_dialogue.json()["messages"]
    )
    assert len(translated_dialogue.json()["messages"]) == 3
    assert len(llm_calls) == llm_call_count

    buyer = _login(client, "ivanov")
    assert (
        client.post(
            f"/communication-testing/{preview.json()['id']}/messages",
            json={"supplier_message": "Test"},
            headers=buyer,
        ).status_code
        == 404
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
        return (
            "Hello. We need 50 kg of ammonia. Please confirm CAS, grade, "
            "form and provide your quote."
        )

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

    buyer = _login(client, "ivanov")
    forbidden = client.post(
        f"/communication-testing/{preview.json()['id']}/escalation-reply",
        json={"message": "Thank you. Let us continue with the quotation."},
        headers=buyer,
    )
    assert forbidden.status_code == 404

    answered = client.post(
        f"/communication-testing/{preview.json()['id']}/escalation-reply",
        json={"message": "Thank you. Let us continue with the quotation."},
        headers=admin,
    )
    assert answered.status_code == 201
    assert answered.json()["status"] == "previewed"
    assert answered.json()["error"] is None
    assert [message["sender_role"] for message in answered.json()["messages"]] == [
        "assistant",
        "supplier",
        "assistant",
    ]
    assert answered.json()["messages"][-1]["delivery_status"] == "manual"
    assert answered.json()["messages"][-1]["content"] == (
        "Thank you. Let us continue with the quotation."
    )
    assert len(dialogue_calls) == 1

    monkeypatch.setattr(
        "app.services.communication_testing.classify_supplier_message",
        lambda *args, **kwargs: CommunicationPolicyDecision(
            auto_reply_allowed=True,
            category="standard_procurement",
            explanation="Обычный ответ по закупке.",
            method="test",
        ),
    )
    resumed = client.post(
        f"/communication-testing/{preview.json()['id']}/messages",
        json={
            "supplier_message": "Our price is USD 700 per ton, CIP Moscow.",
            "confirm_external_send": False,
        },
        headers=admin,
    )
    assert resumed.status_code == 201
    assert resumed.json()["status"] == "previewed"
    assert [message["sender_role"] for message in resumed.json()["messages"]] == [
        "assistant",
        "supplier",
        "assistant",
        "supplier",
        "assistant",
    ]
    assert len(dialogue_calls) == 2

    not_escalated = client.post(
        f"/communication-testing/{preview.json()['id']}/escalation-reply",
        json={"message": "This must not be accepted twice."},
        headers=admin,
    )
    assert not_escalated.status_code == 422


def test_communication_testing_marks_complete_quote_without_followup(
    client, monkeypatch
):
    admin = _login(client)
    generated: list[dict] = []

    def fake_generate_text(self, **kwargs):
        if "переводчик переписки" in kwargs["system_prompt"]:
            return "Цена 720 USD за тонну, MOQ 100 кг, CIP Москва, CoA приложен."
        generated.append(kwargs)
        if len(generated) == 1:
            return "Hello. Please confirm CAS, grade, form and provide your quote."
        return "Thank you. Please confirm how long this quotation remains valid."

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

    started = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "procurement_context": "50 kg of ammonia",
            "delivery_mode": "preview",
        },
        headers=admin,
    )
    assert started.status_code == 201

    completed = client.post(
        f"/communication-testing/{started.json()['id']}/messages",
        json={
            "supplier_message": (
                "USD 720/MT, MOQ: 100 kg, CIP Moscow. USP grade material. "
                "Payment: T/T in advance. Lead time: 15 days. CoA attached."
            )
        },
        headers=admin,
    )

    assert completed.status_code == 201
    payload = completed.json()
    assert payload["status"] == "complete"
    assert [message["sender_role"] for message in payload["messages"]] == [
        "assistant",
        "supplier",
    ]
    assert payload["quote_assessment"] == {
        "is_complete": True,
        "missing_fields": [],
        "low_confidence_fields": [],
        "price": 720.0,
        "currency": "USD",
        "incoterm": "CIP",
        "moq": "100 kg",
        "grade": "USP grade",
        "payment_terms": "T/T",
        "lead_time": "15 days",
        "has_coa": True,
        "has_tds": False,
    }
    assert len(generated) == 1

    blocked = client.post(
        f"/communication-testing/{started.json()['id']}/messages",
        json={"message": "The quotation remains valid for 30 days."},
        headers=admin,
    )
    assert blocked.status_code == 422
    assert "Подтвердите ручное продолжение" in blocked.json()["detail"]

    resumed = client.post(
        f"/communication-testing/{started.json()['id']}/messages",
        json={
            "message": "The quotation remains valid for 30 days.",
            "continue_after_complete": True,
        },
        headers=admin,
    )
    assert resumed.status_code == 201
    assert resumed.json()["status"] == "previewed"
    assert resumed.json()["quote_assessment"]["is_complete"] is True
    assert [message["sender_role"] for message in resumed.json()["messages"]] == [
        "assistant",
        "supplier",
        "supplier",
        "assistant",
    ]

    continued_again = client.post(
        f"/communication-testing/{started.json()['id']}/messages",
        json={"message": "Production can start next week."},
        headers=admin,
    )
    assert continued_again.status_code == 201
    assert continued_again.json()["status"] == "previewed"
    assert continued_again.json()["quote_assessment"]["is_complete"] is True
    assert len(generated) == 3


def test_communication_testing_explains_missing_quote_fields(client, monkeypatch):
    admin = _login(client)
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: (
            "Цена указана, но MOQ и документы отсутствуют."
            if "переводчик переписки" in kwargs["system_prompt"]
            else (
                "Hello. We need 50 kg of ammonia. Please confirm CAS, grade, "
                "form and provide your quote."
            )
        ),
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
    started_response = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "procurement_context": "50 kg of ammonia",
            "delivery_mode": "preview",
        },
        headers=admin,
    )
    assert started_response.status_code == 201
    started = started_response.json()

    premature_resume = client.post(
        f"/communication-testing/{started['id']}/messages",
        json={
            "message": "Our price is USD 720 per MT, CIP Moscow.",
            "continue_after_complete": True,
        },
        headers=admin,
    )
    assert premature_resume.status_code == 422
    assert "только после полного сбора данных" in premature_resume.json()["detail"]

    continued = client.post(
        f"/communication-testing/{started['id']}/messages",
        json={"supplier_message": "Our price is USD 720 per MT, CIP Moscow."},
        headers=admin,
    )

    assert continued.status_code == 201
    assessment = continued.json()["quote_assessment"]
    assert not assessment["is_complete"]
    assert assessment["missing_fields"] == [
        "moq",
        "grade",
        "payment_terms",
        "lead_time",
        "specification",
    ]
    assert len(continued.json()["messages"]) == 3


def test_embedded_communication_test_updates_one_summary_quotation(
    client, monkeypatch
):
    admin = _login(client)
    monkeypatch.setattr(
        "app.services.communication_testing._validate_procurement_identity",
        lambda *args, **kwargs: None,
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
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: "Please also confirm MOQ and CoA availability.",
    )

    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=admin,
    ).json()
    started = client.post(
        "/communication-testing",
        json={
            "rfq_id": rfq["id"],
            "channel": "email",
            "procurement_context": "50 kg Aspirin, CAS 50-78-2",
            "initial_message": "Please quote 50 kg Aspirin, CAS 50-78-2.",
            "delivery_mode": "preview",
        },
        headers=admin,
    ).json()

    first = client.post(
        f"/communication-testing/{started['id']}/messages",
        json={"message": "Our price is USD 720 per MT, CIP Moscow."},
        headers=admin,
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["rfq_id"] == rfq["id"]
    assert first_payload["quotation_id"] is not None

    saved_dialogues = client.get(
        f"/communication-testing?rfq_id={rfq['id']}", headers=admin
    )
    assert saved_dialogues.status_code == 200
    assert [item["id"] for item in saved_dialogues.json()] == [started["id"]]
    assert [
        message["sender_role"] for message in saved_dialogues.json()[0]["messages"]
    ] == ["assistant", "supplier", "assistant"]
    assert client.get(
        f"/communication-testing?rfq_id={rfq['id'] + 1000}", headers=admin
    ).json() == []
    assert client.get(
        "/communication-testing?rfq_id=0", headers=admin
    ).status_code == 422

    first_summary = client.get(
        f"/rfq/{rfq['id']}/summary", headers=admin
    ).json()
    assert len(first_summary) == 1
    assert first_summary[0]["supplier"] == "Тестовый поставщик"
    assert first_summary[0]["price"] == 720.0
    assert not first_summary[0]["is_complete"]

    second = client.post(
        f"/communication-testing/{started['id']}/messages",
        json={
            "message": (
                "MOQ: 100 kg. USP grade material. Payment: T/T in advance. "
                "Lead time: 15 days. CoA attached."
            )
        },
        headers=admin,
    )
    assert second.status_code == 201
    assert second.json()["quotation_id"] == first_payload["quotation_id"]

    final_summary = client.get(
        f"/rfq/{rfq['id']}/summary", headers=admin
    ).json()
    assert len(final_summary) == 1
    assert final_summary[0]["quotation_id"] == first_payload["quotation_id"]
    assert final_summary[0]["price"] == 720.0
    assert final_summary[0]["moq"] == "100 kg"
    assert final_summary[0]["grade"] == "USP grade"
    assert final_summary[0]["payment_terms"] == "T/T"
    assert final_summary[0]["lead_time"] == "15 days"
    assert final_summary[0]["has_coa"] is True
    assert final_summary[0]["is_complete"] is True


def test_buyer_can_use_embedded_test_supplier(client, monkeypatch):
    buyer = _login(client, "ivanov")
    monkeypatch.setattr(
        "app.services.communication_testing._validate_procurement_identity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: "Please quote Aspirin and provide CoA.",
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

    own_rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=buyer,
    ).json()
    preview = client.post(
        "/communication-testing",
        json={
            "rfq_id": own_rfq["id"],
            "channel": "email",
            "procurement_context": "Aspirin, CAS 50-78-2",
            "initial_message": "Please quote Aspirin, CAS 50-78-2.",
            "delivery_mode": "preview",
        },
        headers=buyer,
    )
    assert preview.status_code == 201
    assert preview.json()["delivery_mode"] == "preview"
    assert client.get(
        f"/communication-testing?rfq_id={own_rfq['id']}", headers=buyer
    ).status_code == 200
    assert client.get("/communication-testing", headers=buyer).status_code == 403

    continued = client.post(
        f"/communication-testing/{preview.json()['id']}/messages",
        json={"message": "USD 720/MT, MOQ 100 kg, CIP Moscow. CoA available."},
        headers=buyer,
    )
    assert continued.status_code == 201
    assert continued.json()["quote_assessment"]["price"] == 720.0

    forbidden_send = client.post(
        "/communication-testing",
        json={
            "rfq_id": own_rfq["id"],
            "channel": "email",
            "recipient": "supplier@example.com",
            "procurement_context": "Aspirin, CAS 50-78-2",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=buyer,
    )
    assert forbidden_send.status_code == 403

    forbidden_mode = client.post(
        "/communication-testing",
        json={
            "rfq_id": own_rfq["id"],
            "channel": "email",
            "procurement_context": "Aspirin, CAS 50-78-2",
            "simulation_mode": "supplier_ai",
            "initial_message": "Hello",
            "delivery_mode": "preview",
        },
        headers=buyer,
    )
    assert forbidden_mode.status_code == 403


def test_embedded_dialogue_adds_and_understands_demo_coa(
    client, monkeypatch, tmp_path
):
    import json
    import re

    admin = _login(client)
    monkeypatch.setattr(
        "app.services.communication_testing._validate_procurement_identity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: "Please send your commercial offer and batch document.",
    )
    monkeypatch.setattr(
        "app.services.document_storage.storage_root",
        lambda: tmp_path,
    )

    def fake_document_verification(self, **kwargs):
        document_text = json.loads(kwargs["user_text"])["document"]["document_text"]
        batch = re.search(r"Batch No.: ([A-Z0-9-]+)", document_text).group(1)
        return {
            "document_kind": "coa",
            "substance_match": "exact",
            "verification_status": "confirmed",
            "recommended_action": "accept",
            "confidence": 96,
            "reason": "CAS and batch are stated in the document.",
            "claims": [
                {
                    "claim_type": "chemical_identity",
                    "claim_value": "CAS 50-78-2",
                    "quote": "CAS No.: 50-78-2",
                },
                {
                    "claim_type": "batch",
                    "claim_value": batch,
                    "quote": f"Batch No.: {batch}",
                },
            ],
            "missing_fields": [],
            "red_flags": [],
        }

    monkeypatch.setattr(
        "app.services.document_agent.LLMClient.generate_json",
        fake_document_verification,
    )

    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=admin,
    ).json()
    started = client.post(
        "/communication-testing",
        json={
            "rfq_id": rfq["id"],
            "channel": "email",
            "procurement_context": "Aspirin, CAS 50-78-2",
            "initial_message": "Please quote Aspirin, CAS 50-78-2.",
            "delivery_mode": "preview",
        },
        headers=admin,
    ).json()

    buyer = _login(client, "ivanov")
    assert client.post(
        f"/communication-testing/{started['id']}/demo-document-reply",
        headers=buyer,
    ).status_code == 404

    response = client.post(
        f"/communication-testing/{started['id']}/demo-document-reply",
        headers=admin,
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "complete"
    assert payload["quote_assessment"] == {
        "is_complete": True,
        "missing_fields": [],
        "low_confidence_fields": [],
        "price": 720.0,
        "currency": "USD",
        "incoterm": "CIP",
        "moq": "100 kg",
        "grade": "USP grade",
        "payment_terms": "T/T",
        "lead_time": "15 days",
        "has_coa": True,
        "has_tds": False,
    }
    attachment = payload["messages"][-1]["attachments"][0]
    assert attachment["filename"] == "Demo_CoA_50-78-2.pdf"
    assert attachment["kind"] == "coa"
    assert attachment["status"] == "extracted"
    assert attachment["verification"]["status"] == "confirmed"
    assert attachment["verification"]["cas_in_document"] == ["50-78-2"]
    assert len(attachment["verification"]["accepted_claims"]) == 2

    document_id = attachment["document_id"]
    document = client.get(f"/documents/{document_id}", headers=admin)
    assert document.status_code == 200
    assert "SYNTHETIC DEMONSTRATION ONLY" in document.json()["text_content"]
    downloaded = client.get(f"/documents/{document_id}/file", headers=admin)
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")

    repeated = client.post(
        f"/communication-testing/{started['id']}/demo-document-reply",
        headers=admin,
    )
    assert repeated.status_code == 201
    assert len(repeated.json()["messages"]) == len(payload["messages"])


def test_communication_testing_can_simulate_supplier_for_manual_buyer(
    client, monkeypatch
):
    admin = _login(client)
    supplier_prompts: list[str] = []

    def fake_generate_text(self, **kwargs):
        if "You simulate a chemical supplier" in kwargs["system_prompt"]:
            supplier_prompts.append(kwargs["user_text"])
            return "We can offer USD 720/MT, MOQ: 100 kg, CIP Moscow."
        if "переводчик переписки" in kwargs["system_prompt"]:
            return "Можем предложить 720 USD за тонну, MOQ 100 кг, CIP Москва."
        raise AssertionError("Buyer-agent prompt must not be used")

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        fake_generate_text,
    )

    started = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "procurement_context": "50 kg of ammonia",
            "simulation_mode": "supplier_ai",
            "initial_message": "Hello, please quote 50 kg of ammonia.",
            "delivery_mode": "preview",
        },
        headers=admin,
    )
    assert started.status_code == 201
    assert started.json()["simulation_mode"] == "supplier_ai"
    assert [item["sender_role"] for item in started.json()["messages"]] == [
        "buyer",
        "supplier",
    ]
    assert "BUYER_UNTRUSTED" in supplier_prompts[0]

    continued = client.post(
        f"/communication-testing/{started.json()['id']}/messages",
        json={"message": "Could you also provide CoA?"},
        headers=admin,
    )
    assert continued.status_code == 201
    assert [item["sender_role"] for item in continued.json()["messages"]] == [
        "buyer",
        "supplier",
        "buyer",
        "supplier",
    ]
    assert len(supplier_prompts) == 2

    rejected = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "supplier@example.com",
            "procurement_context": "50 kg of ammonia",
            "simulation_mode": "supplier_ai",
            "initial_message": "Hello",
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert rejected.status_code == 422


def test_communication_testing_uses_saved_rfq_as_first_buyer_message(
    client, monkeypatch
):
    admin = _login(client)
    rfq_body = (
        "Hello,\n\nPlease quote 50 kg of ammonia and provide price, MOQ, "
        "Incoterm and CoA."
    )
    generated: list[str] = []

    def fake_generate_text(self, **kwargs):
        generated.append(kwargs["system_prompt"])
        if "переводчик переписки" in kwargs["system_prompt"]:
            return "Здравствуйте. Просим предоставить предложение."
        raise AssertionError("The saved RFQ must not be regenerated")

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        fake_generate_text,
    )

    started = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "procurement_context": "50 kg of ammonia",
            "simulation_mode": "buyer_ai",
            "initial_message": rfq_body,
            "delivery_mode": "preview",
            "subject": "Request for quotation: ammonia",
        },
        headers=admin,
    )

    assert started.status_code == 201
    assert started.json()["status"] == "previewed"
    assert started.json()["messages"][0]["sender_role"] == "assistant"
    assert started.json()["messages"][0]["content"] == rfq_body
    assert started.json()["messages"][0]["translation_ru"] is None
    assert generated == []

    translated = client.post(
        f"/communication-testing/{started.json()['id']}/translation",
        headers=admin,
    )
    assert translated.status_code == 200
    assert translated.json()["messages"][0]["translation_ru"].startswith("Здравствуйте")
    assert len(generated) == 0
    translated_again = client.post(
        f"/communication-testing/{started.json()['id']}/translation",
        headers=admin,
    )
    assert translated_again.status_code == 200
    assert (
        translated_again.json()["messages"][0]["translation_ru"]
        == translated.json()["messages"][0]["translation_ru"]
    )
    assert len(generated) == 0

    rejected = client.post(
        "/communication-testing",
        json={
            "channel": "email",
            "recipient": "supplier@example.com",
            "procurement_context": "50 kg of ammonia",
            "simulation_mode": "buyer_ai",
            "initial_message": rfq_body,
            "delivery_mode": "send",
            "confirm_external_send": True,
        },
        headers=admin,
    )
    assert rejected.status_code == 422


def test_communication_testing_preserves_reply_when_classifier_is_unavailable(
    client, monkeypatch
):
    admin = _login(client)

    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: (
            "Здравствуйте. Запросите цену."
            if "переводчик переписки" in kwargs["system_prompt"]
            else "Hello. Please confirm CAS, grade, form and provide your price."
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


def test_communication_testing_regenerates_reply_rejected_by_quality_gate(
    client, monkeypatch
):
    admin = _login(client)
    dialogue_outputs = iter(
        (
            "Hello. Please provide your price, Incoterm and lead time.",
            "Hello. Please confirm CAS, grade, form, price, Incoterm and lead time.",
        )
    )
    dialogue_calls = []

    def fake_generate_text(self, **kwargs):
        if "переводчик переписки" in kwargs["system_prompt"]:
            return "Здравствуйте. Подтвердите CAS, сорт, форму, цену и условия."
        dialogue_calls.append(kwargs)
        return next(dialogue_outputs)

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
    assert response.json()["generated_reply"] == (
        "Hello. Please confirm CAS, grade, form, price, Incoterm and lead time."
    )
    assert len(dialogue_calls) == 2
    assert "КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ КАЧЕСТВА" in dialogue_calls[1][
        "additional_instructions"
    ]
    assert "первый RFQ обязан запросить" in dialogue_calls[1][
        "additional_instructions"
    ]


def test_communication_testing_stops_after_repeated_quality_violation(
    client, monkeypatch
):
    admin = _login(client)
    monkeypatch.setattr(
        "app.services.communication_testing.LLMClient.generate_text",
        lambda self, **kwargs: (
            "Hello. Please provide your price, Incoterm and lead time."
        ),
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

    assert response.status_code == 503
    assert "дважды нарушила проверяемые правила" in response.json()["detail"]
    with SessionLocal() as db:
        saved = db.scalar(
            select(CommunicationTestRun).order_by(CommunicationTestRun.id.desc())
        )
        assert saved is not None
        assert saved.status == "llm_error"
        assert saved.messages == []


def test_communication_testing_regenerates_non_english_reply_and_translates_it(
    client, monkeypatch
):
    admin = _login(client)
    generated = iter(
        (
            "Здравствуйте. Сообщите, пожалуйста, цену и срок поставки.",
            "Hello. Please confirm CAS, grade, form, price and lead time.",
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
    assert response.json()["messages"][0]["translation_ru"] is None
    assert len(llm_calls) == 2
    assert "REQUIRED LANGUAGE" in llm_calls[0]["additional_instructions"]
    assert "предыдущая попытка" in llm_calls[1]["additional_instructions"]
    assert "строго соблюдай язык: английском" in llm_calls[1]["additional_instructions"]
    translated = client.post(
        f"/communication-testing/{response.json()['id']}/translation",
        headers=admin,
    )
    assert translated.status_code == 200
    assert translated.json()["messages"][0]["translation_ru"].startswith("Здравствуйте")
    assert len(llm_calls) == 2

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
