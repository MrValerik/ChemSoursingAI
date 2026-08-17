"""Тесты шага 4: поставщики, выбор получателей, рассылка со статусами."""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dispatch.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.connectors.email import IncomingEmail
from app.connectors.whatsapp import WhatsAppDeliveryError
from app.core.db import SessionLocal
from app.extraction.schema import ExtractedQuote
from app.models.communication import Communication
from app.models.escalation import Escalation
from app.models.enums import Channel, CommDirection
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


def test_manual_rfq_draft_is_validated_persisted_and_dispatched(client):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Manual RFQ Supplier",
            "email": "manual-rfq@supplier.example",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "64-17-5", "name": "Ethanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    template_subject = rfq["rfq_subject"]
    template_body = rfq["rfq_body"]
    assert rfq["rfq_is_customized"] is False

    incomplete = client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={"subject": "Only subject", "body": None},
        headers=headers,
    )
    blank = client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={"subject": "   ", "body": "Message"},
        headers=headers,
    )
    assert incomplete.status_code == 422
    assert blank.status_code == 422

    custom_subject = "Custom quotation request for ethanol"
    custom_body = "Dear Supplier,\n\nPlease quote 50 kg of ethanol."
    saved = client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={
            "subject": f"  {custom_subject}  ",
            "body": f"  {custom_body}  ",
        },
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["rfq_subject"] == custom_subject
    assert saved.json()["rfq_body"] == custom_body
    assert saved.json()["rfq_is_customized"] is True

    persisted = client.get(f"/rfq/{rfq['id']}", headers=headers).json()
    assert persisted["rfq_subject"] == custom_subject
    assert persisted["rfq_body"] == custom_body

    auditor = _login(client, "auditor")
    forbidden = client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={"subject": "Forbidden", "body": "Forbidden"},
        headers=auditor,
    )
    assert forbidden.status_code == 403

    reset = client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={"subject": None, "body": None},
        headers=headers,
    )
    assert reset.status_code == 200
    assert reset.json()["rfq_subject"] == template_subject
    assert reset.json()["rfq_body"] == template_body
    assert reset.json()["rfq_is_customized"] is False

    client.put(
        f"/rfq/{rfq['id']}/message-draft",
        json={"subject": custom_subject, "body": custom_body},
        headers=headers,
    )
    client.post(
        f"/rfq/{rfq['id']}/recipients",
        json={"items": [{"supplier_id": supplier["id"], "channel": "email"}]},
        headers=headers,
    )
    dispatched = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers)
    assert dispatched.status_code == 200
    history = _communications(rfq["id"])
    assert len(history) == 1
    assert history[0].subject == f"[RFQ-{rfq['id']}] {custom_subject}"
    assert history[0].body == custom_body


def test_purchase_decision_is_detailed_persisted_and_role_protected(client):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        json={
            "cas": "64-17-5",
            "name": "Ethanol",
            "volume": "500 kg",
            "incoterms": ["CIP"],
        },
        headers=headers,
    ).json()
    quotation = client.post(
        "/quotations",
        json={
            "rfq_id": rfq["id"],
            "price": 12.5,
            "currency": "USD",
            "incoterm": "CIP",
            "moq": "100 kg",
            "grade": "USP",
            "payment_terms": "30% advance, 70% before shipment",
            "lead_time": "15 days",
            "has_coa": True,
            "has_tds": False,
            "field_confidence": {"price": 0.98, "incoterm": 0.95},
        },
        headers=headers,
    ).json()

    summary = client.get(f"/rfq/{rfq['id']}/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()[0]["payment_terms"] == (
        "30% advance, 70% before shipment"
    )
    assert summary.json()[0]["field_confidence"]["price"] == 0.98
    assert summary.json()[0]["created_at"] is not None

    empty = client.get(
        f"/rfq/{rfq['id']}/purchase-decision", headers=headers
    )
    assert empty.status_code == 200
    assert empty.json() is None

    saved = client.put(
        f"/rfq/{rfq['id']}/purchase-decision",
        json={
            "quotation_id": quotation["id"],
            "note": "  Выбрано после технической проверки.  ",
        },
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["quotation_id"] == quotation["id"]
    assert saved.json()["note"] == "Выбрано после технической проверки."
    assert saved.json()["selected_by_name"] == "Иван Иванов"

    persisted = client.get(
        f"/rfq/{rfq['id']}/purchase-decision", headers=headers
    )
    assert persisted.status_code == 200
    assert persisted.json()["id"] == saved.json()["id"]

    another_rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "67-56-1", "name": "Methanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    foreign_quote = client.post(
        "/quotations",
        json={"rfq_id": another_rfq["id"], "price": 8, "currency": "USD"},
        headers=headers,
    ).json()
    wrong_rfq = client.put(
        f"/rfq/{rfq['id']}/purchase-decision",
        json={"quotation_id": foreign_quote["id"]},
        headers=headers,
    )
    assert wrong_rfq.status_code == 422

    auditor = _login(client, "auditor")
    assert (
        client.get(
            f"/rfq/{rfq['id']}/purchase-decision", headers=auditor
        ).status_code
        == 200
    )
    forbidden = client.put(
        f"/rfq/{rfq['id']}/purchase-decision",
        json={"quotation_id": quotation["id"]},
        headers=auditor,
    )
    assert forbidden.status_code == 403


def test_saved_rfq_preview_can_be_translated_without_changing_it(client, monkeypatch):
    headers = _login(client)
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "7664-41-7", "name": "Ammonia", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    before = client.get(f"/rfq/{rfq['id']}", headers=headers).json()
    calls: list[str] = []

    def fake_translate(content: str) -> str:
        calls.append(content)
        return "Тема: Запрос коммерческого предложения\n\nПросим дать цену на аммиак."

    monkeypatch.setattr("app.api.rfq.translate_preview_text", fake_translate)

    translated = client.post(f"/rfq/{rfq['id']}/translation", headers=headers)
    assert translated.status_code == 200
    assert translated.json()["translation_ru"].startswith("Тема:")
    assert before["rfq_subject"] in calls[0]
    assert before["rfq_body"] in calls[0]
    assert client.get(f"/rfq/{rfq['id']}", headers=headers).json() == before

    auditor = _login(client, "auditor")
    assert (
        client.post(f"/rfq/{rfq['id']}/translation", headers=auditor).status_code
        == 200
    )
    assert client.post(f"/rfq/{rfq['id']}/translation").status_code == 401


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

    not_confirmed = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers)
    response = client.post(
        f"/rfq/{rfq['id']}/dispatch?confirm_external_send=true",
        headers=headers,
    )

    assert not_confirmed.status_code == 422
    assert "Подтвердите" in not_confirmed.json()["detail"]
    assert response.status_code == 200
    assert response.json()[0]["status"] == "sent"
    assert response.json()[0]["note"] is None
    history = _communications(rfq["id"])
    assert len(history) == 1
    assert history[0].status == "sent"
    assert history[0].to_address == "live@supplier.example"
    assert history[0].subject.startswith(f"[RFQ-{rfq['id']}]")
    assert history[0].idempotency_key == f"dispatch-{response.json()[0]['id']}"


def test_live_whatsapp_dispatch_requires_confirmation(client, monkeypatch):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Live WhatsApp Supplier",
            "whatsapp": "+7 900 555-01-02",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "67-56-1", "name": "Methanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    selected = client.post(
        f"/rfq/{rfq['id']}/recipients",
        json={"items": [{"supplier_id": supplier["id"], "channel": "whatsapp"}]},
        headers=headers,
    ).json()
    settings = SimpleNamespace(whatsapp_phone_id="123456789")
    monkeypatch.setattr(
        "app.api.suppliers.effective_whatsapp_settings",
        lambda db: (settings, True, "database"),
    )
    sent: list[dict] = []

    def fake_send(self, **kwargs):
        sent.append(kwargs)
        return "wamid.initial-test"

    monkeypatch.setattr(
        "app.api.suppliers.WhatsAppConnector.send_text", fake_send
    )

    not_confirmed = client.post(f"/rfq/{rfq['id']}/dispatch", headers=headers)
    response = client.post(
        f"/rfq/{rfq['id']}/dispatch?confirm_external_send=true",
        headers=headers,
    )

    assert not_confirmed.status_code == 422
    assert response.status_code == 200
    assert response.json()[0]["status"] == "sent"
    assert len(sent) == 1
    history = _communications(rfq["id"])
    assert history[0].external_id == "wamid.initial-test"
    assert history[0].idempotency_key == f"dispatch-{selected[0]['id']}"


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
    monkeypatch.setattr(
        "app.services.email_workflow.store_incoming_attachments",
        lambda *args, **kwargs: [
            {
                "filename": "Methanol_CoA.pdf",
                "content_type": "application/pdf",
                "size": 24576,
                "document_id": 321,
                "kind": "coa",
                "status": "extracted",
                "page_count": 2,
                "error": None,
            }
        ],
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
    overview = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    ).json()
    inbound = overview["conversations"][0]["messages"][0]
    assert inbound["attachments"] == [
        {
            "filename": "Methanol_CoA.pdf",
            "content_type": "application/pdf",
            "size": 24576,
            "document_id": 321,
            "kind": "coa",
            "status": "extracted",
            "page_count": 2,
            "error": None,
        }
    ]
    quotes = client.get(f"/rfq/{rfq['id']}/quotations", headers=headers).json()
    assert len(quotes) == 1
    assert quotes[0]["price"] == 500


def test_email_dialogue_stops_after_cumulative_data_and_coa_attachment(
    client, monkeypatch
):
    headers = _login(client)
    supplier = client.post(
        "/suppliers",
        json={
            "company": "Cumulative Quote Supplier",
            "email": "cumulative@supplier.example",
        },
        headers=headers,
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "67-56-1", "name": "Methanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()

    first = IncomingEmail(
        uid="cumulative-1",
        message_id="<cumulative-1@supplier.example>",
        subject=f"Re: [RFQ-{rfq['id']}] Methanol",
        from_address="cumulative@supplier.example",
        to_addresses=["buyer@example.com"],
        text="USD 500/MT, CIP Moscow.",
    )
    second = IncomingEmail(
        uid="cumulative-2",
        message_id="<cumulative-2@supplier.example>",
        subject=f"Re: [RFQ-{rfq['id']}] Methanol",
        from_address="cumulative@supplier.example",
        to_addresses=["buyer@example.com"],
        text="Our MOQ is 1 MT. CoA attached.",
        attachments=[{"filename": "methanol-coa.pdf", "content": b"test"}],
    )

    class FakeConnector:
        settings = SimpleNamespace(
            auto_followup_mode="draft",
            email_delivery_mode="demo",
            email_from="buyer@example.com",
        )

        def __init__(self):
            self.pending = [[first], [second]]
            self.seen = []

        def fetch_unseen(self, limit=20):
            return self.pending.pop(0)

        def mark_seen(self, uids):
            self.seen.extend(uids)

    extracted = iter(
        [
            ExtractedQuote(
                price=500,
                currency="USD",
                incoterm="CIP",
                field_confidence={"price": 0.9, "incoterm": 0.9},
                method="test",
            ),
            ExtractedQuote(
                moq="1 MT",
                field_confidence={"moq": 0.95},
                method="test",
            ),
        ]
    )
    monkeypatch.setattr(
        "app.services.email_workflow.extract_quote",
        lambda *args, **kwargs: next(extracted),
    )
    monkeypatch.setattr(
        "app.services.email_workflow.classify_supplier_message",
        lambda *args, **kwargs: CommunicationPolicyDecision(
            auto_reply_allowed=True,
            category="standard_procurement",
            explanation="Standard quotation reply.",
            method="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.email_workflow._render_followup",
        lambda *args, **kwargs: "Please provide the remaining details.",
    )
    monkeypatch.setattr(
        "app.services.email_workflow.store_incoming_attachments",
        lambda *args, attachments=None, **kwargs: (
            [
                {
                    "filename": "methanol-coa.pdf",
                    "content_type": "application/pdf",
                    "size": 4,
                    "document_id": 777,
                    "kind": "coa",
                    "status": "extracted",
                    "page_count": 1,
                    "error": None,
                }
            ]
            if attachments
            else []
        ),
    )

    connector = FakeConnector()
    with SessionLocal() as db:
        first_result = sync_inbox(db, connector=connector)
        second_result = sync_inbox(db, connector=connector)

    assert first_result.followups_drafted == 1
    assert second_result.followups_drafted == 0
    assert second_result.followups_sent == 0
    assert connector.seen == ["cumulative-1", "cumulative-2"]
    history = _communications(rfq["id"])
    assert [item.status for item in history] == [
        "received",
        "cancelled",
        "received",
    ]

    overview = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    ).json()
    conversation = next(
        item
        for item in overview["conversations"]
        if item["supplier_id"] == supplier["id"]
    )
    assert conversation["data_collection_status"] == "complete"
    assert conversation["missing_quote_fields"] == []


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


def _started_conversation(client, headers, *, channel: str, contact: str):
    supplier_payload = {"company": f"Manual {channel} Supplier", channel: contact}
    supplier = client.post(
        "/suppliers", json=supplier_payload, headers=headers
    ).json()
    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "64-17-5", "name": "Ethanol", "incoterms": ["CIP"]},
        headers=headers,
    ).json()
    client.post(
        f"/rfq/{rfq['id']}/recipients",
        json={"items": [{"supplier_id": supplier["id"], "channel": channel}]},
        headers=headers,
    )
    dispatched = client.post(
        f"/rfq/{rfq['id']}/dispatch", headers=headers
    )
    assert dispatched.status_code == 200
    first = _communications(rfq["id"])[0]
    assert first.manager_id is not None
    return rfq, first


def test_manual_email_message_is_live_idempotent_and_threaded(client, monkeypatch):
    headers = _login(client)
    rfq, first = _started_conversation(
        client,
        headers,
        channel="email",
        contact="manual-email@supplier.example",
    )
    settings = SimpleNamespace(
        email_delivery_mode="live",
        email_from="buyer@example.com",
    )
    monkeypatch.setattr(
        "app.services.communication_delivery.effective_email_settings",
        lambda db: (settings, True, "database"),
    )
    sent: list[dict] = []

    def fake_send(self, **kwargs):
        sent.append(kwargs)
        return "<manual-reply@example.com>"

    monkeypatch.setattr(
        "app.services.communication_delivery.EmailConnector.send", fake_send
    )
    payload = {
        "manager_id": first.manager_id,
        "channel": "email",
        "body": "Please confirm the lead time.",
        "idempotency_key": "3f8bd99f-e8cd-4976-bad6-0d3bdce07e5b",
        "confirm_external_send": True,
    }

    response = client.post(
        f"/rfq/{rfq['id']}/communications/send",
        json=payload,
        headers=headers,
    )
    repeated = client.post(
        f"/rfq/{rfq['id']}/communications/send",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 201
    assert repeated.status_code == 201
    assert response.json()["status"] == "sent"
    assert len(sent) == 1
    assert sent[0]["to_address"] == "manual-email@supplier.example"
    assert sent[0]["subject"].startswith("Re: [RFQ-")
    history = _communications(rfq["id"])
    assert len(history) == 2
    assert history[-1].external_id == "<manual-reply@example.com>"
    assert history[-1].idempotency_key == payload["idempotency_key"]

    auditor = _login(client, "auditor")
    assert (
        client.post(
            f"/rfq/{rfq['id']}/communications/send",
            json={**payload, "idempotency_key": "ba26298b-0fef-461b-b96f-af0404c705cb"},
            headers=auditor,
        ).status_code
        == 403
    )


def test_real_supplier_dialogue_translation_is_temporary_and_scoped(
    client, monkeypatch
):
    headers = _login(client)
    rfq, first = _started_conversation(
        client,
        headers,
        channel="email",
        contact="translate@supplier.example",
    )
    monkeypatch.setattr(
        "app.services.communication_translation.GoogleTranslateConnector.translate",
        lambda self, text, **kwargs: f"RU: {text}",
    )

    response = client.post(
        f"/rfq/{rfq['id']}/communications/translation",
        json={"message_ids": [first.id, first.id]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "translations": [
            {"message_id": first.id, "translation_ru": f"RU: {first.body}"}
        ]
    }
    assert _communications(rfq["id"])[0].body == first.body

    other_rfq, other_message = _started_conversation(
        client,
        headers,
        channel="whatsapp",
        contact="+7 900 555-01-02",
    )
    assert other_rfq["id"] != rfq["id"]
    foreign = client.post(
        f"/rfq/{rfq['id']}/communications/translation",
        json={"message_ids": [other_message.id]},
        headers=headers,
    )
    assert foreign.status_code == 422


def test_real_supplier_dialogue_translation_reports_provider_error(
    client, monkeypatch
):
    headers = _login(client)
    rfq, first = _started_conversation(
        client,
        headers,
        channel="email",
        contact="translate-error@supplier.example",
    )

    def fail_translation(self, text, **kwargs):
        from app.connectors.google_translate import GoogleTranslateError

        raise GoogleTranslateError("Google Translate недоступен")

    monkeypatch.setattr(
        "app.services.communication_translation.GoogleTranslateConnector.translate",
        fail_translation,
    )
    response = client.post(
        f"/rfq/{rfq['id']}/communications/translation",
        json={"message_ids": [first.id]},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Google Translate недоступен"


def test_manual_whatsapp_message_records_provider_error_without_retry(
    client, monkeypatch
):
    headers = _login(client)
    rfq, first = _started_conversation(
        client,
        headers,
        channel="whatsapp",
        contact="+7 900 100-20-30",
    )
    settings = SimpleNamespace(whatsapp_phone_id="123456789")
    monkeypatch.setattr(
        "app.services.communication_delivery.effective_whatsapp_settings",
        lambda db: (settings, True, "database"),
    )
    attempts: list[str] = []

    def fail_send(self, *, to_number, body):
        attempts.append(to_number)
        raise WhatsAppDeliveryError("WhatsApp отклонил тестовое сообщение")

    monkeypatch.setattr(
        "app.services.communication_delivery.WhatsAppConnector.send_text", fail_send
    )
    payload = {
        "manager_id": first.manager_id,
        "channel": "whatsapp",
        "body": "Could you confirm availability?",
        "idempotency_key": "0f6e55a4-bcf4-4548-a75d-ce1e3084fb32",
        "confirm_external_send": True,
    }

    response = client.post(
        f"/rfq/{rfq['id']}/communications/send",
        json=payload,
        headers=headers,
    )
    repeated = client.post(
        f"/rfq/{rfq['id']}/communications/send",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 503
    assert repeated.status_code == 503
    assert len(attempts) == 1
    assert _communications(rfq["id"])[-1].status == "delivery_error"


def test_send_saved_email_draft(client, monkeypatch):
    headers = _login(client)
    rfq, first = _started_conversation(
        client,
        headers,
        channel="email",
        contact="draft@supplier.example",
    )
    with SessionLocal() as db:
        draft = Communication(
            rfq_id=rfq["id"],
            manager_id=first.manager_id,
            direction=CommDirection.OUTBOUND,
            channel=Channel.EMAIL,
            subject=f"Re: [RFQ-{rfq['id']}] Ethanol",
            body="Please provide MOQ.",
            from_address=None,
            to_address="draft@supplier.example",
            status="draft",
            thread_id="<supplier-reply@example.com>",
            external_id=None,
            attachments=None,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

    settings = SimpleNamespace(
        email_delivery_mode="live",
        email_from="buyer@example.com",
    )
    monkeypatch.setattr(
        "app.services.communication_delivery.effective_email_settings",
        lambda db: (settings, True, "database"),
    )
    monkeypatch.setattr(
        "app.services.communication_delivery.EmailConnector.send",
        lambda self, **kwargs: "<sent-draft@example.com>",
    )

    response = client.post(
        f"/communications/{draft_id}/send",
        json={"confirm_external_send": True},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert response.json()["body"] == "Please provide MOQ."
