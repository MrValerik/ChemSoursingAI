"""Shared mailbox threads: exact addresses, complete history, no RFQ inference."""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.communications import router
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import Communication, RFQ
from app.models.base import Base
from app.models.enums import Channel, CommDirection, UserRole


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([RFQ(id=30, name="Synthetic A"), RFQ(id=31, name="Synthetic B")])
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role=UserRole.BUYER)
    with TestClient(app) as client:
        yield client


def add_mail(db, address="sales@supplier.example", *, outbound=False, day=10, **kwargs):
    message = Communication(
        direction=CommDirection.OUTBOUND if outbound else CommDirection.INBOUND,
        channel=kwargs.pop("channel", Channel.EMAIL),
        from_address="buyer@company.example" if outbound else address,
        to_address=address if outbound else "buyer@company.example",
        subject=kwargs.pop("subject", "Synthetic quotation"),
        body=kwargs.pop("body", "Synthetic message body"),
        status="sent" if outbound else "received",
        message_at=kwargs.pop("message_at", datetime(2026, 8, day, 12)),
        created_at=kwargs.pop("created_at", datetime(2026, 8, day, 12)),
        **kwargs,
    )
    db.add(message)
    db.commit()
    return message


def test_legacy_messages_and_replies_group_by_exact_normalized_address(client, db):
    old = add_mail(db, '"Sales team" <Sales@Supplier.Example>', day=1,
                   rfq_id=30, external_id="<old@example>", thread_id="original")
    reply = add_mail(db, "sales@supplier.example", outbound=True, day=2,
                     rfq_id=30, thread_id="<old@example>")
    new = add_mail(db, "SALES@SUPPLIER.EXAMPLE", day=3, rfq_id=31, subject="Different subject")
    unresolved = add_mail(db, "sales@supplier.example", day=4)
    other = add_mail(db, "sales4@supplier.example", day=5)
    add_mail(db, "sales@supplier.example", channel=Channel.WHATSAPP, day=6)

    result = client.get("/mail/threads").json()
    assert result["total"] == 2
    assert result["total_messages"] == 5
    thread = result["items"][1]
    assert thread["correspondent"] == "sales@supplier.example"
    assert thread["message_count"] == thread["matched_count"] == 4
    assert thread["rfq_ids"] == [30, 31]
    assert thread["unresolved_count"] == 1
    assert result["items"][0]["latest_message"]["id"] == other.id

    response = client.get(f"/mail/messages/{old.id}/thread")
    assert response.status_code == 200
    history = response.json()
    assert [m["id"] for m in history["items"]] == [old.id, reply.id, new.id, unresolved.id]
    assert [m["rfq_id"] for m in history["items"]] == [30, 30, 31, None]
    assert history["items"][-1]["is_unresolved"] is True
    assert history["next_before_id"] is None
    db.expire_all()
    assert db.get(Communication, old.id).thread_id == "original"
    assert db.get(Communication, old.id).external_id == "<old@example>"
    assert db.get(Communication, unresolved.id).rfq_id is None


@pytest.mark.parametrize("params,matched,latest_day", [
    ({"folder": "inbox"}, 2, 12),
    ({"folder": "sent"}, 1, 13),
    ({"folder": "unresolved"}, 1, 12),
    ({"date_from": "2026-08-12", "date_to": "2026-08-12"}, 1, 12),
    ({"query": "UNIQUE NEEDLE"}, 1, 10),
    ({"query": "SALES@SUPPLIER.EXAMPLE"}, 3, 13),
])
def test_filters_select_threads_but_do_not_cut_history(client, db, params, matched, latest_day):
    old = add_mail(db, day=10, body="Unique needle", rfq_id=30)
    add_mail(db, day=12)
    add_mail(db, day=13, outbound=True, rfq_id=30)
    result = client.get("/mail/threads", params=params).json()
    assert result["total"] == 1
    assert result["total_messages"] == matched
    thread = result["items"][0]
    assert thread["matched_count"] == matched
    assert thread["message_count"] == 3
    assert thread["latest_message"]["message_at"].startswith(f"2026-08-{latest_day:02d}")
    history = client.get(f"/mail/messages/{old.id}/thread").json()
    assert history["total"] == len(history["items"]) == 3


def test_missing_or_invalid_addresses_remain_separate(client, db):
    messages = [add_mail(db, address) for address in [None, "", "unknown", "unknown"]]
    result = client.get("/mail/threads").json()
    assert result["total"] == 4
    assert all(t["correspondent"] is None and t["message_count"] == 1 for t in result["items"])
    for message in messages:
        history = client.get(f"/mail/messages/{message.id}/thread").json()
        assert history["key"] == f"message:{message.id}"
        assert [m["id"] for m in history["items"]] == [message.id]


def test_thread_pagination_counts_correspondents_not_messages(client, db):
    add_mail(db, "old@supplier.example", day=1)
    add_mail(db, "middle@supplier.example", day=2)
    for _ in range(55):
        add_mail(db, "new@supplier.example", day=3)
    first = client.get("/mail/threads", params={"limit": 1}).json()
    second = client.get("/mail/threads", params={"limit": 1, "offset": 1}).json()
    assert first["total"] == second["total"] == 3
    assert first["total_messages"] == 57
    assert first["items"][0]["message_count"] == 55
    assert second["items"][0]["correspondent"] == "middle@supplier.example"
    assert client.get("/mail/threads", params={"offset": 100}).json()["items"] == []


def test_history_pagination_stable_when_new_mail_arrives(client, db):
    messages = [add_mail(db, day=1) for _ in range(55)]
    path = f"/mail/messages/{messages[0].id}/thread"
    first = client.get(path).json()
    assert [m["id"] for m in first["items"]] == [m.id for m in messages[5:]]
    assert first["next_before_id"] == messages[5].id
    add_mail(db, day=2)
    older = client.get(path, params={"before_id": first["next_before_id"]}).json()
    assert [m["id"] for m in older["items"]] == [m.id for m in messages[:5]]
    assert older["total"] == 56
    assert older["next_before_id"] is None
    assert client.get(path, params={"before_id": messages[0].id}).json()["items"] == []


def test_old_message_date_fallback_and_attachments(client, db):
    old = add_mail(db, message_at=None, day=1, attachments=[{
        "filename": "synthetic.pdf", "size": 2048, "content_type": "application/pdf",
        "document_id": 123,
    }])
    result = client.get("/mail/threads", params={"date_to": "2026-08-01"}).json()
    assert result["items"][0]["latest_message"]["message_at"].startswith("2026-08-01")
    history = client.get(f"/mail/messages/{old.id}/thread").json()
    assert history["items"][0]["attachments"][0]["document_id"] == 123


def test_invalid_requests_and_other_channel_do_not_leak_history(client, db):
    mail = add_mail(db)
    other = add_mail(db, "other@supplier.example")
    whatsapp = add_mail(db, channel=Channel.WHATSAPP)
    assert client.get("/mail/messages/999999/thread").status_code == 404
    assert client.get(f"/mail/messages/{whatsapp.id}/thread").status_code == 404
    assert client.get(f"/mail/messages/{mail.id}/thread", params={"before_id": other.id}).status_code == 422
    for params in [{"folder": "bad"}, {"limit": 0}, {"limit": 101}, {"offset": -1},
                   {"date_from": "2026-08-12", "date_to": "2026-08-01"}]:
        assert client.get("/mail/threads", params=params).status_code == 422
    assert client.get("/mail/threads", params={"query": "no match"}).json()["total"] == 0


def test_auth_required_and_auditor_can_read_but_not_send(client, db):
    mail = add_mail(db)
    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/mail/threads").status_code == 401
    assert client.get(f"/mail/messages/{mail.id}/thread").status_code == 401
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.AUDITOR)
    assert client.get("/mail/threads").status_code == 200
    assert client.get(f"/mail/messages/{mail.id}/thread").status_code == 200
    assert client.post("/mail/messages", json={
        "to_address": "sales@supplier.example", "subject": "Synthetic", "body": "Test",
        "idempotency_key": str(uuid4()), "confirm_external_send": True,
    }).status_code == 403


def test_reply_uses_chosen_message_not_newest_rfq_in_thread(client, db, monkeypatch):
    from app.services import mailbox

    old = add_mail(db, 'Sales <SALES@SUPPLIER.EXAMPLE>', day=1,
                   rfq_id=30, external_id="<old-rfq30@example>")
    add_mail(db, day=2, rfq_id=31, external_id="<new-rfq31@example>")
    settings = SimpleNamespace(email_delivery_mode="live", email_from="buyer@company.example")
    monkeypatch.setattr(mailbox, "effective_email_settings", lambda db: (settings, True, None))
    sent = []

    class FakeEmailConnector:
        def __init__(self, settings):
            pass

        def send(self, **kwargs):
            sent.append(kwargs)
            return kwargs["message_id"]

    monkeypatch.setattr(mailbox, "EmailConnector", FakeEmailConnector)
    payload = {
        "to_address": "sales@supplier.example", "subject": "Re: Synthetic quotation",
        "body": "Synthetic reply", "idempotency_key": str(uuid4()),
        "reply_to_message_id": old.id,
    }
    assert client.post("/mail/messages", json=payload).status_code == 422
    assert sent == []
    payload["confirm_external_send"] = True
    result = client.post("/mail/messages", json=payload)
    assert result.status_code == 201
    assert result.json()["rfq_id"] == 30
    assert client.post("/mail/messages", json=payload).json()["id"] == result.json()["id"]
    assert len(sent) == 1
    assert sent[0]["in_reply_to"] == "<old-rfq30@example>"
    assert sent[0]["references"] == ["<old-rfq30@example>"]
    history = client.get(f"/mail/messages/{old.id}/thread").json()
    assert history["total"] == 3
    assert len(list(db.scalars(select(Communication)))) == 3
