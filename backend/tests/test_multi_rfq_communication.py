"""Одна подтверждённая цепочка переписки видна из нескольких RFQ."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Communication,
    CommunicationRfqLink,
    Manager,
    RFQ,
    RfqBatch,
    RfqRecipient,
    Supplier,
)
from app.models.enums import Channel, CommDirection, DispatchStatus
from app.services.combined_communication import (
    combined_options,
    dispatch_combined_message,
    prepare_combined_message,
)
from app.services.communication_history import list_communication_overview
from app.services.communication_links import link_communication_to_rfqs
from app.services.communication_translation import translate_communication_messages


class FakeTranslator:
    def translate(self, text: str, **_: str) -> str:
        return f"RU: {text}"


def test_shared_message_is_visible_and_translatable_from_each_linked_rfq() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = RFQ(name="Aspirin")
        second = RFQ(name="Citric acid")
        supplier = Supplier(company="Explicit Multi Product Supplier")
        manager = Manager(
            full_name="Sales",
            email="sales@multi-product.example",
            supplier=supplier,
        )
        db.add_all([first, second, manager])
        db.flush()
        message = Communication(
            rfq_id=None,
            manager_id=manager.id,
            direction=CommDirection.OUTBOUND,
            channel=Channel.EMAIL,
            subject="Two explicitly selected RFQ positions",
            body="Please quote both explicitly listed positions.",
            to_address=manager.email,
            status="sent",
        )
        db.add(message)
        linked_ids = link_communication_to_rfqs(
            db,
            communication=message,
            rfq_ids=[first.id, second.id, first.id],
        )
        db.commit()

        assert linked_ids == [first.id, second.id]
        assert db.scalars(select(CommunicationRfqLink)).all()
        for rfq in (first, second):
            overview = list_communication_overview(db, rfq.id)
            assert len(overview.conversations) == 1
            assert [item.id for item in overview.conversations[0].messages] == [
                message.id
            ]
            translations = translate_communication_messages(
                db,
                rfq_id=rfq.id,
                message_ids=[message.id],
                translator=FakeTranslator(),
            )
            assert translations[0].translation_ru.startswith("RU:")


def test_link_requires_an_explicit_rfq_selection() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        message = Communication(
            rfq_id=None,
            manager_id=None,
            direction=CommDirection.OUTBOUND,
            channel=Channel.EMAIL,
            body="No implicit name matching.",
            status="draft",
        )
        db.add(message)
        try:
            link_communication_to_rfqs(db, communication=message, rfq_ids=[])
        except ValueError as exc:
            assert "выбрать хотя бы один RFQ" in str(exc)
        else:
            raise AssertionError("Empty explicit selection must be rejected")


def test_combined_dispatch_requires_selected_recipients_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        batch = RfqBatch(idempotency_key="batch-combined", source_name="items.csv")
        first = RFQ(
            name="Aspirin",
            cas="50-78-2",
            volume="100 kg",
            incoterms=["CIP"],
            batch=batch,
        )
        second = RFQ(
            name="Citric acid",
            cas="77-92-9",
            volume="200 kg",
            incoterms=["CIP"],
            batch=batch,
        )
        supplier = Supplier(company="Explicit Multi Product Supplier")
        manager = Manager(
            full_name="Sales",
            email="sales@multi-product.example",
            supplier=supplier,
        )
        db.add_all([first, second, manager])
        db.flush()
        recipients = [
            RfqRecipient(
                rfq_id=rfq.id,
                supplier_id=supplier.id,
                channel=Channel.EMAIL,
                status=DispatchStatus.QUEUED,
            )
            for rfq in (first, second)
        ]
        db.add_all(recipients)
        db.commit()

        options = combined_options(db, rfqs=[first, second])
        assert len(options) == 1
        assert [item["rfq_id"] for item in options[0]["positions"]] == [
            first.id,
            second.id,
        ]

        prepared = prepare_combined_message(
            db,
            batch_id=batch.id,
            supplier_id=supplier.id,
            channel=Channel.EMAIL,
            rfq_ids=[second.id, first.id],
        )
        assert f"RFQ-{first.id}" in prepared.body
        assert f"RFQ-{second.id}" in prepared.body
        assert "not mixed between products" in prepared.body

        key = "b7f615eb-c044-4f85-aacd-0827611a7526"
        sent = dispatch_combined_message(
            db,
            prepared=prepared,
            idempotency_key=key,
            confirm_external_send=False,
        )
        assert sent.status == "demo"
        assert {link.rfq_id for link in sent.rfq_links} == {first.id, second.id}
        assert all(item.status == DispatchStatus.SENT for item in recipients)

        replay_prepared = prepare_combined_message(
            db,
            batch_id=batch.id,
            supplier_id=supplier.id,
            channel=Channel.EMAIL,
            rfq_ids=[first.id, second.id],
            accept_sent_recipients=True,
        )
        replay = dispatch_combined_message(
            db,
            prepared=replay_prepared,
            idempotency_key=key,
            confirm_external_send=False,
        )
        assert replay.id == sent.id
        assert db.scalars(select(Communication)).all() == [sent]
