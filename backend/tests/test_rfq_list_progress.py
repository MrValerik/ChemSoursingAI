"""Список «Запросы» отдаёт ход работ, а не только статус.

Проверяются агрегаты ручки GET /rfq: момент рассылки, разбор переписки по
компаниям, ошибки доставки и причины эскалаций.
"""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_list_progress.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models import Manager
from app.models.communication import Communication
from app.models.enums import (
    Channel,
    CommDirection,
    DispatchStatus,
    EscalationReason,
    EscalationStatus,
)
from app.models.escalation import Escalation
from app.models.recipient import RfqRecipient
from app.models.rfq_supplier import RfqSupplierLink
from app.models.supplier import Supplier
from app.services.rfq_progress import (
    ACTION_DISPATCH,
    ACTION_DISPATCH_ERROR,
    ACTION_ESCALATION,
    ACTION_REPLY,
    ACTION_VERIFY,
    SILENCE_DAYS,
)

DB_FILE = "test_rfq_list_progress.db"


@pytest.fixture(scope="module")
def client():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    with TestClient(app) as c:
        yield c
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)


def _login(client):
    resp = client.post(
        "/auth/login", json={"username": "ivanov", "password": "demo123"}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_rfq(client, headers, name):
    resp = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": name, "incoterms": ["CIP"]},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _row(client, headers, rfq_id):
    listed = client.get("/rfq", headers=headers).json()
    return next(r for r in listed if r["id"] == rfq_id)


def _supplier(db, company):
    supplier = Supplier(company=company, country="China")
    db.add(supplier)
    db.flush()
    manager = Manager(full_name=f"{company} sales", supplier_id=supplier.id)
    db.add(manager)
    db.flush()
    return supplier, manager


def test_draft_row_asks_to_verify(client):
    headers = _login(client)
    rfq_id = _create_rfq(client, headers, "Aspirin draft")
    row = _row(client, headers, rfq_id)
    assert row["next_action"] == ACTION_VERIFY
    assert row["stage"] == "search"
    assert row["dispatched_at"] is None
    assert row["waiting_days"] is None
    assert row["n_suppliers_found"] == 0


def test_found_suppliers_switch_action_to_dispatch(client):
    headers = _login(client)
    rfq_id = _create_rfq(client, headers, "Aspirin found")
    with SessionLocal() as db:
        from app.models.rfq import RFQ

        rfq = db.get(RFQ, rfq_id)
        rfq.verified = True
        supplier, _ = _supplier(db, "Found Chem")
        excluded, _ = _supplier(db, "Rejected Chem")
        db.add(RfqSupplierLink(rfq_id=rfq_id, supplier_id=supplier.id))
        db.add(
            RfqSupplierLink(
                rfq_id=rfq_id, supplier_id=excluded.id, status="excluded"
            )
        )
        db.commit()

    row = _row(client, headers, rfq_id)
    # Снятая вручную компания в знаменатель рассылки не попадает.
    assert row["n_suppliers_found"] == 1
    assert row["next_action"] == ACTION_DISPATCH


def test_dispatch_moment_and_delivery_error_are_visible(client):
    headers = _login(client)
    rfq_id = _create_rfq(client, headers, "Aspirin sent")
    sent_at = datetime.now(timezone.utc) - timedelta(days=2)
    with SessionLocal() as db:
        first, _ = _supplier(db, "Alpha Chem")
        second, _ = _supplier(db, "Beta Chem")
        db.add(
            RfqRecipient(
                rfq_id=rfq_id,
                supplier_id=first.id,
                channel=Channel.EMAIL,
                status=DispatchStatus.SENT,
                created_at=sent_at,
                updated_at=sent_at,
            )
        )
        db.add(
            RfqRecipient(
                rfq_id=rfq_id,
                supplier_id=second.id,
                channel=Channel.EMAIL,
                status=DispatchStatus.ERROR,
                created_at=sent_at + timedelta(minutes=1),
                updated_at=sent_at + timedelta(minutes=1),
            )
        )
        db.commit()

    row = _row(client, headers, rfq_id)
    assert row["n_recipients"] == 2
    assert row["dispatched_at"] is not None
    assert row["waiting_days"] == 2
    assert row["n_silent"] == 2
    assert row["n_dispatch_errors"] == 1
    assert row["next_action"] == ACTION_DISPATCH_ERROR


def test_reply_without_quotation_still_counts_as_answer(client):
    """Вопрос поставщика не создаёт котировку, но переписка уже идёт."""
    headers = _login(client)
    rfq_id = _create_rfq(client, headers, "Aspirin dialogue")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        answering, answering_manager = _supplier(db, "Talkative Chem")
        silent, _ = _supplier(db, "Silent Chem")
        for supplier in (answering, silent):
            db.add(
                RfqRecipient(
                    rfq_id=rfq_id,
                    supplier_id=supplier.id,
                    channel=Channel.EMAIL,
                    status=DispatchStatus.SENT,
                    created_at=now - timedelta(days=SILENCE_DAYS + 2),
                    updated_at=now - timedelta(days=SILENCE_DAYS + 2),
                )
            )
        db.add(
            Communication(
                rfq_id=rfq_id,
                manager_id=answering_manager.id,
                direction=CommDirection.OUTBOUND,
                channel=Channel.EMAIL,
                body="RFQ",
                message_at=now - timedelta(days=SILENCE_DAYS + 2),
            )
        )
        db.add(
            Communication(
                rfq_id=rfq_id,
                manager_id=answering_manager.id,
                direction=CommDirection.INBOUND,
                channel=Channel.EMAIL,
                body="What grade do you need?",
                message_at=now - timedelta(hours=3),
            )
        )
        db.commit()

    row = _row(client, headers, rfq_id)
    assert row["n_quotations"] == 0
    assert row["n_suppliers_replied"] == 1
    assert row["n_silent"] == 1
    assert row["n_awaiting_our_reply"] == 1
    # Ответ поставщика важнее молчания остальных.
    assert row["next_action"] == ACTION_REPLY
    assert row["last_inbound_at"] is not None
    assert row["waiting_days"] == 0


def test_open_escalation_carries_its_reason(client):
    headers = _login(client)
    rfq_id = _create_rfq(client, headers, "Aspirin escalated")
    with SessionLocal() as db:
        db.add(
            Escalation(
                rfq_id=rfq_id,
                reason=EscalationReason.GRADE,
                status=EscalationStatus.OPEN,
            )
        )
        db.commit()

    row = _row(client, headers, rfq_id)
    assert row["has_open_escalation"] is True
    assert row["escalation_reasons"] == [EscalationReason.GRADE.value]
    assert row["next_action"] == ACTION_ESCALATION
