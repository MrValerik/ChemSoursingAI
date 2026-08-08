from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base, CommunicationTestMessage, CommunicationTestRun, User
from app.models.enums import UserRole
from app.services.communication_recipient import protect_recipient, recipient_key
from app.services.communication_test_whatsapp import (
    accept_incoming_whatsapp,
    process_incoming_whatsapp,
)


class FakeLLM:
    model = "communication-cloud-test"

    def generate_json(self, **kwargs: object) -> dict:
        text = str(kwargs.get("user_text", ""))
        social = "weekends" in text
        return {
            "route": "escalate" if social else "auto_reply",
            "category": "social_or_personal" if social else "standard_procurement",
            "explanation": "Нестандартный вопрос." if social else "Условия закупки.",
        }

    def generate_text(self, **kwargs: object) -> str:
        if "переводчик переписки" in str(kwargs.get("system_prompt", "")):
            return "Перевод для пользователя."
        return "Thank you. Please confirm the lead time and Incoterm."


@pytest.fixture()
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'communication-whatsapp.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_run(db: Session, number: str = "+7 900 000-00-00") -> CommunicationTestRun:
    actor = User(
        username="admin-whatsapp-test",
        full_name="Administrator",
        password_hash="unused",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(actor)
    db.flush()
    run = CommunicationTestRun(
        actor_id=actor.id,
        channel="whatsapp",
        recipient_masked="*******0000",
        recipient_key=recipient_key("whatsapp", number),
        recipient_ciphertext=protect_recipient(number),
        procurement_context="50 kg ammonia, price and CoA required",
        subject="Ammonia RFQ",
        customer_message="50 kg ammonia, price and CoA required",
        reply_language="en",
        delivery_mode="send",
        status="sent",
    )
    run.messages.append(
        CommunicationTestMessage(
            sender_role="assistant",
            content="Please quote 50 kg ammonia.",
            delivery_status="sent",
            provider_message_id="initial-web-message",
        )
    )
    db.add(run)
    db.commit()
    return run


def _configure(monkeypatch, sent: list[tuple[str, str]]) -> None:
    import app.services.communication_test_whatsapp as service

    settings = get_settings().model_copy(
        update={
            "whatsapp_transport": "web",
            "whatsapp_web_base_url": "http://gateway:3000",
            "whatsapp_web_service_token": "gateway-secret",
        }
    )
    monkeypatch.setattr(service, "_communication_test_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(
        service, "effective_whatsapp_settings", lambda _db: (settings, True, "environment")
    )

    def send_text(_self, *, to_number: str, body: str) -> str:
        sent.append((to_number, body))
        return "outbound-web-message"

    monkeypatch.setattr(service.WhatsAppConnector, "send_text", send_text)


def test_standard_incoming_message_is_matched_deduplicated_and_replied(
    session_factory, monkeypatch
) -> None:
    sent: list[tuple[str, str]] = []
    _configure(monkeypatch, sent)
    with session_factory() as db:
        run = _create_run(db)
        state, run_id = accept_incoming_whatsapp(
            db,
            message_id="incoming-web-message",
            from_number="79000000000",
            body="We can supply it at USD 2/kg, MOQ 50 kg.",
        )
        duplicate, _ = accept_incoming_whatsapp(
            db,
            message_id="incoming-web-message",
            from_number="79000000000",
            body="We can supply it at USD 2/kg, MOQ 50 kg.",
        )

        assert state == "accepted"
        assert duplicate == "duplicate"
        assert run_id == run.id
        process_incoming_whatsapp(db, run_id=run.id, message_id="incoming-web-message")

        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert saved.status == "sent"
        assert sent == [("+7 900 000-00-00", "Thank you. Please confirm the lead time and Incoterm.")]
        assert [message.sender_role for message in saved.messages] == [
            "assistant",
            "supplier",
            "assistant",
        ]


def test_social_message_escalates_without_sending(session_factory, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    _configure(monkeypatch, sent)
    with session_factory() as db:
        run = _create_run(db)
        state, _ = accept_incoming_whatsapp(
            db,
            message_id="social-web-message",
            from_number="79000000000",
            body="What do you do on weekends?",
        )
        assert state == "accepted"

        process_incoming_whatsapp(db, run_id=run.id, message_id="social-web-message")

        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert saved.status == "escalated"
        assert "Требуется ответ человека" in (saved.error or "")
        assert sent == []
