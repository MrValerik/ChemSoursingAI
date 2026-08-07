from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.email import IncomingEmail
from app.models import Base, CommunicationTestMessage, CommunicationTestRun, User
from app.models.enums import UserRole
from app.services.communication_test_email import sync_communication_test_email


class FakeLLM:
    model = "communication-cloud-test"

    def generate_json(self, **_: object) -> dict:
        return {
            "route": "auto_reply",
            "category": "standard_procurement",
            "explanation": "Сообщение относится к условиям закупки.",
        }

    def generate_text(self, **_: object) -> str:
        return "Thank you. Please confirm the lead time and Incoterm."


class FakeConnector:
    def __init__(
        self,
        messages: list[IncomingEmail],
        *,
        send_error: Exception | None = None,
    ) -> None:
        self.messages = messages
        self.send_error = send_error
        self.sent: list[dict] = []
        self.seen: list[str] = []

    def fetch_unseen(self, limit: int = 20) -> list[IncomingEmail]:
        return self.messages[:limit]

    def send(self, **kwargs: object) -> str:
        self.sent.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        return "<outbound-followup@example.com>"

    def mark_seen(self, uids: list[str]) -> None:
        self.seen.extend(uids)


@pytest.fixture()
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'communication-email.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_sent_run(db: Session) -> CommunicationTestRun:
    actor = User(
        username="admin-email-test",
        full_name="Administrator",
        password_hash="unused",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(actor)
    db.flush()
    run = CommunicationTestRun(
        actor_id=actor.id,
        channel="email",
        recipient_masked="su***@example.com",
        procurement_context="50 kg ammonia, price and CoA required",
        subject="Ammonia RFQ",
        customer_message="50 kg ammonia, price and CoA required",
        reply_language="en",
        delivery_mode="send",
        status="sent",
        provider_message_id="<initial@example.com>",
    )
    run.messages.append(
        CommunicationTestMessage(
            sender_role="assistant",
            content="Please quote 50 kg ammonia.",
            delivery_status="sent",
            provider_message_id="<initial@example.com>",
        )
    )
    db.add(run)
    db.commit()
    return run


def _incoming(
    *,
    message_id: str = "<incoming@example.com>",
    text: str = "We can supply 50 kg at USD 2/kg, MOQ 50 kg.",
    reference: str = "<initial@example.com>",
) -> IncomingEmail:
    return IncomingEmail(
        uid="101",
        message_id=message_id,
        subject="Re: Ammonia RFQ",
        from_address="supplier@example.com",
        to_addresses=["buyer@example.com"],
        text=text,
        in_reply_to=reference,
        references=[reference],
    )


def test_standard_reply_is_threaded_and_processed_once(session_factory) -> None:
    with session_factory() as db:
        run = _create_sent_run(db)
        connector = FakeConnector([_incoming()])

        first = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )
        second = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )

        assert first.processed == 1
        assert first.replied == 1
        assert second.duplicates == 1
        assert len(connector.sent) == 1
        assert connector.sent[0]["to_address"] == "supplier@example.com"
        assert connector.sent[0]["in_reply_to"] == "<incoming@example.com>"
        assert "<incoming@example.com>" in connector.sent[0]["references"]
        assert connector.seen == ["101", "101"]

        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert saved.status == "sent"
        assert saved.provider_message_id == "<outbound-followup@example.com>"
        assert [message.sender_role for message in saved.messages] == [
            "assistant",
            "supplier",
            "assistant",
        ]
        assert saved.messages[-1].delivery_status == "sent"


def test_social_question_escalates_without_sending(session_factory) -> None:
    with session_factory() as db:
        run = _create_sent_run(db)
        connector = FakeConnector(
            [_incoming(text="How are you? What do you do on weekends?")]
        )

        summary = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )

        assert summary.processed == 1
        assert summary.escalated == 1
        assert not connector.sent
        assert connector.seen == ["101"]
        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert saved.status == "escalated"
        assert "Требуется ответ человека" in (saved.error or "")
        assert len(saved.messages) == 2


def test_ambiguous_smtp_failure_is_not_retried(session_factory) -> None:
    with session_factory() as db:
        run = _create_sent_run(db)
        connector = FakeConnector(
            [_incoming()], send_error=TimeoutError("ambiguous SMTP result")
        )

        first = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )
        second = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )

        assert first.processed == 1
        assert first.errors == ["<incoming@example.com>: TimeoutError"]
        assert second.duplicates == 1
        assert len(connector.sent) == 1
        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert saved.status == "delivery_error"
        assert saved.messages[-1].delivery_status == "delivery_error"


def test_restart_resumes_before_smtp_without_duplicating_inbound(
    session_factory,
) -> None:
    with session_factory() as db:
        run = _create_sent_run(db)
        run.messages.append(
            CommunicationTestMessage(
                sender_role="supplier",
                content="We can supply 50 kg at USD 2/kg, MOQ 50 kg.",
                delivery_status="received",
                provider_message_id="<incoming@example.com>",
            )
        )
        run.status = "classifying"
        db.commit()
        connector = FakeConnector([_incoming()])

        summary = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )

        assert summary.processed == 1
        assert summary.replied == 1
        assert len(connector.sent) == 1
        db.expire_all()
        saved = db.get(CommunicationTestRun, run.id)
        assert saved is not None
        assert [message.sender_role for message in saved.messages] == [
            "assistant",
            "supplier",
            "assistant",
        ]


def test_unmatched_email_stays_unseen(session_factory) -> None:
    with session_factory() as db:
        _create_sent_run(db)
        connector = FakeConnector(
            [_incoming(reference="<unrelated@example.com>")]
        )

        summary = sync_communication_test_email(
            db, connector, llm=FakeLLM()
        )

        assert summary.unmatched == 1
        assert not connector.sent
        assert not connector.seen
        assert db.scalar(
            select(CommunicationTestMessage.id).where(
                CommunicationTestMessage.provider_message_id
                == "<incoming@example.com>"
            )
        ) is None
