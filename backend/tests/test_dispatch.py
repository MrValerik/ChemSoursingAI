"""Тесты шага 4: поставщики, выбор получателей, рассылка со статусами."""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dispatch.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.connectors.email import IncomingEmail
from app.core.db import SessionLocal
from app.extraction.schema import ExtractedQuote
from app.services.email_workflow import sync_inbox


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
    assert client.post("/email/sync", headers=headers).status_code == 403


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
    history = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    ).json()
    assert len(history) == 1
    assert history[0]["status"] == "sent"
    assert history[0]["to_address"] == "live@supplier.example"
    assert history[0]["subject"].startswith(f"[RFQ-{rfq['id']}]")


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
    history = client.get(
        f"/rfq/{rfq['id']}/communications", headers=headers
    ).json()
    assert [item["status"] for item in history] == ["received", "draft"]
    quotes = client.get(f"/rfq/{rfq['id']}/quotations", headers=headers).json()
    assert len(quotes) == 1
    assert quotes[0]["price"] == 500
