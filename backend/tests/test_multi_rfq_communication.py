"""Одна подтверждённая цепочка переписки видна из нескольких RFQ."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Communication, CommunicationRfqLink, Manager, RFQ, Supplier
from app.models.enums import Channel, CommDirection
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
