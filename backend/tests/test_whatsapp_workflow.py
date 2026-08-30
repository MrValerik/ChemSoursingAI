from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.extraction.schema import ExtractedQuote
from app.models import Base, Manager, Quotation, RFQ, Supplier
from app.models.communication import Communication
from app.models.enums import Channel, CommDirection, RFQStatus
from app.models.escalation import Escalation
from app.services.communication_policy import CommunicationPolicyDecision
from app.services.whatsapp_workflow import (
    accept_business_whatsapp,
    process_business_whatsapp,
    store_unmatched_whatsapp,
)


@pytest.fixture()
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'whatsapp-workflow.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _conversation(
    db: Session,
    *,
    phone: str = "+7 900 555-01-02",
    message_id: str = "outbound-1",
    created_at: datetime | None = None,
) -> tuple[RFQ, Manager, Communication]:
    supplier = Supplier(company=f"Supplier {message_id}")
    manager = Manager(
        full_name="Sales manager",
        whatsapp=phone,
        supplier=supplier,
    )
    rfq = RFQ(name=f"Product {message_id}", cas="64-17-5")
    db.add_all([supplier, rfq])
    db.flush()
    outbound = Communication(
        rfq_id=rfq.id,
        manager_id=manager.id,
        direction=CommDirection.OUTBOUND,
        channel=Channel.WHATSAPP,
        body="Please send your quotation.",
        to_address=phone,
        status="sent",
        external_id=message_id,
        thread_id=message_id,
        created_at=created_at,
    )
    db.add(outbound)
    db.commit()
    return rfq, manager, outbound


def test_quoted_reply_is_linked_to_exact_rfq_and_deduplicated(session_factory) -> None:
    with session_factory() as db:
        first, manager, outbound = _conversation(db, message_id="outbound-first")
        _conversation(
            db,
            phone=manager.whatsapp or "",
            message_id="outbound-latest",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )

        accepted = accept_business_whatsapp(
            db,
            message_id="incoming-exact",
            from_number="79005550102",
            body="USD 12/kg, CIP Moscow",
            timestamp=1_800_000_000,
            quoted_message_id=outbound.external_id,
        )
        duplicate = accept_business_whatsapp(
            db,
            message_id="incoming-exact",
            from_number="79005550102",
            body="USD 12/kg, CIP Moscow",
            timestamp=1_800_000_000,
            quoted_message_id=outbound.external_id,
        )

        assert accepted.state == "accepted"
        assert accepted.rfq_id == first.id
        assert duplicate.state == "duplicate"
        inbound = db.get(Communication, accepted.communication_id)
        assert inbound is not None
        assert inbound.manager_id == manager.id
        assert inbound.status == "received"


def test_unquoted_reply_for_shared_number_is_visible_but_escalated(session_factory) -> None:
    with session_factory() as db:
        old, manager, _ = _conversation(
            db,
            message_id="outbound-old",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        latest, _, _ = _conversation(
            db,
            phone=manager.whatsapp or "",
            message_id="outbound-new",
            created_at=datetime.now(timezone.utc),
        )

        result = accept_business_whatsapp(
            db,
            message_id="incoming-ambiguous",
            from_number="+7 (900) 555-01-02",
            body="Our price is USD 11/kg.",
            timestamp=1_800_000_001,
        )

        assert result.state == "ambiguous"
        assert result.rfq_id == latest.id
        assert result.rfq_id != old.id
        inbound = db.get(Communication, result.communication_id)
        escalation = db.scalar(
            select(Escalation).where(Escalation.communication_id == inbound.id)
        )
        assert inbound.status == "received_ambiguous"
        assert escalation is not None
        assert "несколькими диалогами RFQ" in (escalation.note or "")
        assert db.get(RFQ, latest.id).status == RFQStatus.ESCALATED


def test_incoming_whatsapp_attachment_is_stored_for_dialogue(
    session_factory, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "app.services.document_storage.storage_root", lambda: tmp_path / "documents"
    )
    with session_factory() as db:
        rfq, _, outbound = _conversation(db, message_id="outbound-document")
        result = accept_business_whatsapp(
            db,
            message_id="incoming-document",
            from_number="79005550102",
            body="CoA attached",
            timestamp=1_800_000_002,
            quoted_message_id=outbound.external_id,
            attachments=[
                {
                    "filename": "coa.txt",
                    "content_type": "text/plain",
                    "size": 11,
                    "content_base64": base64.b64encode(b"CAS 64-17-5").decode(),
                }
            ],
        )

        inbound = db.get(Communication, result.communication_id)
        assert result.rfq_id == rfq.id
        assert inbound.attachments[0]["filename"] == "coa.txt"
        assert inbound.attachments[0]["document_id"] > 0
        assert any((tmp_path / "documents").rglob("*.bin"))


def test_unknown_number_is_preserved_without_creating_a_fake_rfq(session_factory) -> None:
    with session_factory() as db:
        result = store_unmatched_whatsapp(
            db,
            message_id="incoming-unknown",
            from_number="8613800138000",
            body="Hello",
            timestamp=1_800_000_003,
        )
        inbound = db.get(Communication, result.communication_id)
        assert result.state == "unmatched"
        assert inbound.rfq_id is None
        assert inbound.manager_id is None
        assert inbound.status == "unresolved"


def test_standard_reply_is_extracted_into_quotation(
    session_factory, monkeypatch
) -> None:
    class FakeLLM:
        def take_usage(self):
            return 0, 0

    monkeypatch.setattr(
        "app.services.whatsapp_workflow.LLMClient", lambda: FakeLLM()
    )
    monkeypatch.setattr(
        "app.services.whatsapp_workflow.classify_supplier_message",
        lambda *args, **kwargs: CommunicationPolicyDecision(
            auto_reply_allowed=True,
            category="standard_procurement",
            explanation="Обычный ответ на RFQ.",
            method="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.whatsapp_workflow.extract_quote",
        lambda *args, **kwargs: ExtractedQuote(
            price=12,
            currency="USD",
            incoterm="CIP",
            moq="100 kg",
            grade="99%",
            payment_terms="T/T",
            lead_time="7 days",
            has_coa=True,
            field_confidence={
                "price": 0.95,
                "currency": 0.95,
                "incoterm": 0.95,
                "moq": 0.95,
                "grade": 0.95,
                "payment_terms": 0.95,
                "lead_time": 0.95,
                "has_coa": 0.95,
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.whatsapp_workflow.get_rfq_prompt_context",
        lambda *args, **kwargs: (None, None),
    )

    with session_factory() as db:
        rfq, manager, outbound = _conversation(db, message_id="outbound-quote")
        result = accept_business_whatsapp(
            db,
            message_id="incoming-quote",
            from_number="79005550102",
            body="USD 12/kg, CIP, MOQ 100 kg, T/T, 7 days, grade 99%.",
            timestamp=1_800_000_004,
            quoted_message_id=outbound.external_id,
        )
        created = process_business_whatsapp(
            db, communication_id=result.communication_id
        )

        quote = db.scalar(
            select(Quotation).where(
                Quotation.source_communication_id == result.communication_id
            )
        )
        assert created == 1
        assert quote is not None
        assert quote.rfq_id == rfq.id
        assert quote.manager_id == manager.id
        assert float(quote.price) == 12
        assert db.get(RFQ, rfq.id).status == RFQStatus.PARSED
