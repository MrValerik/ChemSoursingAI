"""Одна подтверждённая цепочка переписки видна из нескольких RFQ."""

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.connectors.email import IncomingEmail
from app.extraction.schema import ExtractedQuote
from app.models import (
    Base,
    Communication,
    CommunicationPolicyAudit,
    CommunicationRfqLink,
    Manager,
    RFQ,
    RfqBatch,
    RfqRecipient,
    Supplier,
    Quotation,
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
from app.services.communication_policy import CommunicationPolicyDecision
from app.services.email_workflow import _split_multi_rfq_sections, sync_inbox


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
            assert {
                item.rfq_id
                for item in overview.conversations[0].messages[0].linked_rfqs
            } == {first.id, second.id}
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


def test_section_splitter_never_inherits_unlabelled_or_duplicate_text() -> None:
    sections, duplicates = _split_multi_rfq_sections(
        "Common price USD 999/kg\nRFQ-10: USD 10/kg\nRFQ-11: USD 20/kg",
        {10, 11},
    )
    assert "999" not in sections[10]
    assert "20" not in sections[10]
    assert "10" not in sections[11]
    assert duplicates == set()

    _, duplicates = _split_multi_rfq_sections(
        "RFQ-10: first\nRFQ-10: second",
        {10},
    )
    assert duplicates == {10}


def test_inbound_combined_reply_creates_isolated_quotes(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        batch = RfqBatch(idempotency_key="batch-inbound")
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
        supplier = Supplier(company="Reply Isolation Supplier")
        manager = Manager(
            full_name="Sales",
            email="reply@isolated.example",
            supplier=supplier,
        )
        db.add_all([first, second, manager])
        db.flush()
        db.add_all(
            [
                RfqRecipient(
                    rfq_id=rfq.id,
                    supplier_id=supplier.id,
                    channel=Channel.EMAIL,
                    status=DispatchStatus.QUEUED,
                )
                for rfq in (first, second)
            ]
        )
        db.flush()
        prepared = prepare_combined_message(
            db,
            batch_id=batch.id,
            supplier_id=supplier.id,
            channel=Channel.EMAIL,
            rfq_ids=[first.id, second.id],
        )
        outbound = dispatch_combined_message(
            db,
            prepared=prepared,
            idempotency_key="ff65c1d6-e1be-4e96-967b-905b73331b82",
            confirm_external_send=False,
        )
        outbound.external_id = "<combined-out@isolated.example>"
        db.commit()

        class FakeConnector:
            settings = SimpleNamespace(
                auto_followup_mode="off",
                email_delivery_mode="demo",
                email_from="buyer@example.com",
            )
            seen: list[str] = []

            def fetch_unseen(self, limit=20):
                return [
                    IncomingEmail(
                        uid="combined-1",
                        message_id="<combined-in@isolated.example>",
                        subject="Re: multiple products",
                        from_address=manager.email,
                        to_addresses=["buyer@example.com"],
                        text=(
                            "Shared note USD 999/kg must be ignored.\n"
                            f"RFQ-{first.id}: Price USD 10/kg, CIP Moscow.\n"
                            f"RFQ-{second.id}: Price USD 20/kg, CIP Moscow."
                        ),
                        in_reply_to=outbound.external_id,
                    )
                ]

            def mark_seen(self, uids):
                self.seen.extend(uids)

        class FakeLlm:
            def take_usage(self):
                return (0, 0)

        monkeypatch.setattr(
            "app.services.email_workflow.communication_llm_client",
            lambda: FakeLlm(),
        )
        monkeypatch.setattr(
            "app.services.email_workflow.classify_supplier_message",
            lambda *args, **kwargs: CommunicationPolicyDecision(
                auto_reply_allowed=True,
                category="standard_procurement",
                explanation="Safe labelled section.",
                method="test",
            ),
        )

        def fake_extract(text, **kwargs):
            price = 10 if "USD 10/kg" in text else 20
            return ExtractedQuote(
                price=price,
                currency="USD",
                incoterm="CIP",
                price_unit="kg",
                field_confidence={"price": 0.95, "incoterm": 0.95},
                method="test",
            )

        monkeypatch.setattr("app.services.email_workflow.extract_quote", fake_extract)
        monkeypatch.setattr(
            "app.services.email_workflow.store_incoming_attachments",
            lambda *args, **kwargs: [],
        )
        connector = FakeConnector()
        result = sync_inbox(db, connector=connector)

        quotes = list(db.scalars(select(Quotation).order_by(Quotation.rfq_id)))
        assert result.processed == 1
        assert result.quotations_created == 2
        assert connector.seen == ["combined-1"]
        assert [(quote.rfq_id, float(quote.price)) for quote in quotes] == [
            (first.id, 10.0),
            (second.id, 20.0),
        ]
        audits = list(
            db.scalars(
                select(CommunicationPolicyAudit).order_by(
                    CommunicationPolicyAudit.rfq_id
                )
            )
        )
        summaries = {
            audit.rfq_id: audit.budget_snapshot["safe_input_summary"]
            for audit in audits
        }
        assert "999" not in summaries[first.id]
        assert "20/kg" not in summaries[first.id]
        assert "10/kg" not in summaries[second.id]
