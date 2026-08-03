"""Тесты шага 4: поставщики, выбор получателей, рассылка со статусами."""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dispatch.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.connectors.email import IncomingEmail
from app.core.db import SessionLocal
from app.extraction.schema import ExtractedQuote
from app.models.communication import Communication
from app.models.escalation import Escalation
from app.services.communication_policy import CommunicationPolicyDecision
from app.services.email_workflow import sync_inbox


def _communications(rfq_id: int) -> list[Communication]:
    """Переписка запроса: HTTP-ручки истории больше нет, читаем из базы."""
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Communication)
                .where(Communication.rfq_id == rfq_id)
                .order_by(Communication.created_at, Communication.id)
            ).all()
        )


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_dispatch.db"):
        os.remove("test_dispatch.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_dispatch.db"):
        os.remove("test_dispatch.db")


def _login(client, username="ivanov"):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_supplier_registry_seeded(client):
    headers = _login(client)
    suppliers = client.get("/suppliers", headers=headers).json()
    assert len(suppliers) >= 3
    haihua = next(s for s in suppliers if s["company"] == "Shandong Haihua")
    assert "email" in haihua["channels"]


def test_add_supplier_manually(client):
    headers = _login(client)
    resp = client.post(
        "/suppliers",
        json={"company": "Test Chem GmbH", "email": "sales@test.example"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "добавлен вручную"
    assert resp.json()["channels"] == ["email"]


def test_supplier_registry_includes_filters_and_request_metrics(client):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        headers=headers,
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
    ).json()
    supplier = client.post(
        f"/suppliers?rfq_id={rfq['id']}",
        headers=headers,
        json={
            "company": "Evidence Metrics Chemical",
            "type": "manufacturer",
            "country": "China",
            "email": "metrics@supplier.example",
            "source": "https://metrics.example/product",
            "qualification_status": "candidate",
            "evidence_score": 78,
            "certificates": ["ISO 9001"],
        },
    ).json()
    assert supplier["qualification_status"] == "candidate"

    registry = client.get("/suppliers", headers=headers).json()
    item = next(row for row in registry if row["id"] == supplier["id"])
    assert item["qualification_status"] == "candidate"
    assert item["evidence_score"] == 78
    assert item["last_checked_at"] is not None
    assert item["contacts_count"] == 1
    assert item["request_count"] == 1
    assert item["linked_requests"] == [
        {"rfq_id": rfq["id"], "name": "Aspirin", "cas": "50-78-2"}
    ]
    assert item["certificates"] == ["ISO 9001"]

    duplicate = client.post(
        f"/suppliers?rfq_id={rfq['id']}",
        headers=headers,
        json={
            "company": "Duplicate title from the same source",
            "source": "https://metrics.example/product",
            "evidence_score": 82,
        },
    ).json()
    assert duplicate["id"] == supplier["id"]
    assert duplicate["request_count"] == 1


def test_select_and_dispatch(client):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    suppliers = client.get("/suppliers", headers=headers).json()
    s1, s2 = suppliers[0], suppliers[1]

    # Выбор получателей (идемпотентный).
    payload = {
        "items": [
            {"supplier_id": s1["id"], "channel": "email"},
            {"supplier_id": s2["id"], "channel": "email"},
        ]
    }
    recipients = client.post(
        f"/rfq/{rfq['id']}/recipients", json=payload, headers=headers
    ).json()
    assert len(recipients) == 2
    assert all(r["status"] == "queued" for r in recipients)
    recipients = client.post(
        f"/rfq/{rfq['id']}/recipients", json=payload, headers=headers
    ).json()
    assert len(recipients) == 2  # повтор не дублирует

    # Отмена одного, пока в очереди.
    resp = client.delete(
        f"/rfq/{rfq['id']}/recipients/{recipients[1]['id']}", headers=headers
    )
    assert resp.status_code == 204

    # Рассылка: queued -> sent, статус RFQ -> sent.
    sent = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers).json()
    assert len(sent) == 1
    assert sent[0]["status"] == "sent"
    updated = client.get(f"/rfq/{rfq['id']}", headers=headers).json()
    assert updated["status"] == "sent"
    overview = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    ).json()
    assert len(overview["conversations"]) == 1
    assert overview["conversations"][0]["supplier_id"] == s1["id"]
    assert overview["conversations"][0]["recipient_status"] == "sent"
    assert overview["conversations"][0]["messages"][0]["status"] == "demo"

    # После отправки отмена недоступна, повторная рассылка — 422 (очередь пуста).
    resp = client.delete(
        f"/rfq/{rfq['id']}/recipients/{sent[0]['id']}", headers=headers
    )
    assert resp.status_code == 422
    assert client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers).status_code == 422


def test_live_smtp_dispatch_creates_communication(client, monkeypatch):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Live Email Supplier",
            "email": "live@supplier.example",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "64-17-5", "name": "Ethanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    client.post(
        f"/rfq/{rfq['id']}/recipients",
        json={"items": [{"supplier_id": supplier["id"], "channel": "email"}]},
        headers=headers,
    )
    settings = SimpleNamespace(
        email_delivery_mode="live",
        email_from="buyer@example.com",
    )
    monkeypatch.setattr(
        "app.api.suppliers.effective_email_settings",
        lambda db: (settings, True, "environment"),
    )
    monkeypatch.setattr(
        "app.api.suppliers.EmailConnector.send",
        lambda self, **kwargs: "<rfq-live@example.com>",
    )

    response = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers)

    assert response.status_code == 200
    assert response.json()[0]["status"] == "sent"
    history = _communications(rfq["id"])
    assert len(history) == 1
    assert history[0].status == "sent"
    assert history[0].to_address == "live@supplier.example"
    assert history[0].subject.startswith(f"[RFQ-{rfq['id']}]")


def test_imap_reply_creates_quote_and_followup_draft(client, monkeypatch):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Inbound Email Supplier",
            "email": "reply@supplier.example",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "67-56-1", "name": "Methanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()

    class FakeConnector:
        seen: list[str] = []

        def fetch_unseen(self, limit=20):
            return [
                IncomingEmail(
                    uid="500",
                    message_id="<reply-500@supplier.example>",
                    subject=f"Re: [RFQ-{rfq['id']}] Methanol",
                    from_address="reply@supplier.example",
                    to_addresses=["buyer@example.com"],
                    text="Price USD 500/MT, CIP Moscow.",
                )
            ]

        def mark_seen(self, uids):
            self.seen.extend(uids)

    monkeypatch.setattr(
        "app.services.email_workflow.extract_quote",
        lambda *args, **kwargs: ExtractedQuote(
            price=500,
            currency="USD",
            incoterm="CIP",
            field_confidence={"price": 0.9, "incoterm": 0.9},
            method="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.email_workflow.classify_supplier_message",
        lambda *args, **kwargs: CommunicationPolicyDecision(
            auto_reply_allowed=True,
            category="standard_procurement",
            explanation="Стандартный ответ по котировке.",
            method="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.email_workflow._render_followup",
        lambda *args, **kwargs: "Please provide MOQ and CoA.",
    )
    monkeypatch.setattr(
        "app.services.email_workflow.effective_email_settings",
        lambda db: (
            SimpleNamespace(
                auto_followup_mode="draft",
                email_delivery_mode="demo",
                email_from="buyer@example.com",
            ),
            False,
            "environment",
        ),
    )
    connector = FakeConnector()
    with SessionLocal() as db:
        result = sync_inbox(db, connector=connector)

    assert result.processed == 1
    assert result.quotations_created == 1
    assert result.followups_drafted == 1
    assert connector.seen == ["500"]
    history = _communications(rfq["id"])
    assert [item.status for item in history] == ["received", "draft"]
    quotes = client.get(f"/rfq/{rfq['id']}/quotations", headers=headers).json()
    assert len(quotes) == 1
    assert quotes[0]["price"] == 500


def test_nonstandard_supplier_question_creates_escalation_without_reply(
    client, monkeypatch
):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Escalation Supplier",
            "email": "escalate@supplier.example",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "75-07-0", "name": "Acetaldehyde", "incoterms": ["CIP"]},
        headers=headers,
    ).json()

    class FakeConnector:
        settings = SimpleNamespace(
            auto_followup_mode="send",
            email_delivery_mode="live",
            email_from="buyer@example.com",
        )
        sent = []

        def fetch_unseen(self, limit=20):
            return [
                IncomingEmail(
                    uid="501",
                    message_id="<social-501@supplier.example>",
                    subject=f"Re: [RFQ-{rfq['id']}] Acetaldehyde",
                    from_address="escalate@supplier.example",
                    to_addresses=["buyer@example.com"],
                    text="Hello, how are you?",
                )
            ]

        def mark_seen(self, uids):
            self.seen = uids

        def send(self, **kwargs):
            self.sent.append(kwargs)
            return "<must-not-send@example.com>"

    monkeypatch.setattr(
        "app.services.email_workflow.extract_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Nonstandard message must not be extracted or answered")
        ),
    )
    connector = FakeConnector()
    with SessionLocal() as db:
        result = sync_inbox(db, connector=connector)
        escalation = db.scalar(
            select(Escalation).where(Escalation.rfq_id == rfq["id"])
        )

    assert result.processed == 1
    assert result.escalations_created == 1
    assert result.quotations_created == 0
    assert result.followups_drafted == 0
    assert result.followups_sent == 0
    assert connector.sent == []
    assert escalation is not None
    assert escalation.communication_id is not None
    assert escalation.manager_id is not None
    assert "Автоответ остановлен" in escalation.note

    overview = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    )
    assert overview.status_code == 200
    conversations = overview.json()["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["supplier_id"] == supplier["id"]
    assert conversations[0]["supplier_company"] == "Escalation Supplier"
    assert conversations[0]["messages"][0]["body"] == "Hello, how are you?"
    assert conversations[0]["escalations"][0]["status"] == "open"
    assert client.get(f"/rfq/{rfq['id']}", headers=headers).json()["status"] == "escalated"


def test_email_sync_is_restricted_to_head_and_admin(client, monkeypatch):
    buyer = _login(client)
    assert (
        client.post("/communications/email/sync", headers=buyer).status_code
        == 403
    )

    monkeypatch.setattr(
        "app.api.communications.sync_inbox",
        lambda db, limit=20: SimpleNamespace(
            as_dict=lambda: {
                "fetched": 1,
                "processed": 1,
                "duplicates": 0,
                "unmatched": 0,
                "quotations_created": 0,
                "followups_drafted": 0,
                "followups_sent": 0,
                "escalations_created": 1,
                "errors": [],
            }
        ),
    )
    admin = _login(client, "admin")
    response = client.post("/communications/email/sync", headers=admin)
    assert response.status_code == 200
    assert response.json()["escalations_created"] == 1
