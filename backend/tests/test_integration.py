"""Сквозной интеграционный тест на SQLite in-memory.

Прогоняет всю цепочку ядра без внешних зависимостей (без PubChem, без LLM):
RFQ → генерация письма → извлечение котировки из ответа → контроль полноты →
авто-эскалация → сводная сравнительная таблица.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.extraction.pipeline import extract_quote
from app.models import Base
from app.models.enums import EscalationReason, RFQStatus, SupplierType
from app.models.escalation import Escalation
from app.models.manager import Manager
from app.models.supplier import Supplier
from app.schemas.quotation import QuotationCreate
from app.schemas.rfq import RFQCreate
from app.services.quotation_service import build_summary, create_quotation
from app.services.rfq_service import create_rfq, render_rfq_text

# Синтетические ответы поставщиков.
REPLY_COMPLETE = (
    "For CAS 50-78-2, our price is USD 12.50/kg CIP Moscow. MOQ 25 kg. "
    "USP grade material. Payment: T/T in advance. We provide CoA and technical "
    "data sheet. Lead time 15 days."
)
REPLY_SHORTAGE = "Sorry, this item is currently out of stock. We will quote later."


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _store_reply(db, rfq_id: int, text: str):
    q = extract_quote(text, use_llm=False)  # без модели — путь на правилах
    data = QuotationCreate(
        rfq_id=rfq_id,
        price=q.price,
        currency=q.currency,
        incoterm=q.incoterm,
        moq=q.moq,
        grade=q.grade,
        payment_terms=q.payment_terms,
        lead_time=q.lead_time,
        manufacturer=q.manufacturer,
        origin_country=q.origin_country,
        packaging=q.packaging,
        price_unit=q.price_unit,
        quoted_quantity=q.quoted_quantity,
        total_price=q.total_price,
        delivery_cost=q.delivery_cost,
        duty_cost=q.duty_cost,
        vat_cost=q.vat_cost,
        landed_cost=q.landed_cost,
        cost_currency=q.cost_currency,
        is_hazmat=q.is_hazmat,
        has_coa=q.has_coa,
        has_tds=q.has_tds,
        field_confidence=q.field_confidence,
        source_text=text,
    )
    return create_quotation(db, data)


def test_full_sourcing_chain(db):
    # 1. Создание RFQ (offline, без верификации PubChem).
    rfq = create_rfq(
        db,
        RFQCreate(
            cas="50-78-2",
            name="Acetylsalicylic acid",
            incoterms=["CIP", "FCA", "EXW"],
            channels=["email"],
        ),
        verify=False,
    )
    assert rfq.id is not None
    assert rfq.status == RFQStatus.DRAFT

    # 2. Генерация письма RFQ.
    subject, body = render_rfq_text(rfq)
    assert "50-78-2" in subject
    assert "CIP" in body and "Certificate of Analysis (CoA)" in body

    # 3. Извлечение и сохранение двух ответов.
    q_full = _store_reply(db, rfq.id, REPLY_COMPLETE)
    _store_reply(db, rfq.id, REPLY_SHORTAGE)

    # Полная котировка распознана корректно, базис сохранён как есть.
    assert q_full.price == 12.5
    assert q_full.incoterm == "CIP"
    assert q_full.is_complete is True

    # 4. Авто-эскалация по «out of stock».
    escalations = db.scalars(select(Escalation).where(Escalation.rfq_id == rfq.id)).all()
    assert any(e.reason == EscalationReason.SHORTAGE for e in escalations)

    # 5. Сводная таблица: полная котировка выше неполной.
    summary = build_summary(db, rfq.id)
    assert len(summary) == 2
    assert summary[0].is_complete is True
    assert summary[0].incoterm == "CIP"
    assert summary[1].is_complete is False

    # Статус RFQ переведён в SUMMARIZED после сводки (если был в потоке сбора).
    # (здесь стартовали с DRAFT, поэтому проверяем, что сводка не падает)
    assert isinstance(summary, list)


def test_incoterm_preserves_non_target_basis(db):
    """Поставщик ответил на FOB (вне CIP/FCA/EXW) — базис не теряется."""
    rfq = create_rfq(
        db,
        RFQCreate(cas="50-78-2", name="X", incoterms=["CIP"]),
        verify=False,
    )
    q = _store_reply(db, rfq.id, "Price USD 20/kg FOB Ningbo. MOQ 200 kg. CoA included.")
    assert q.incoterm == "FOB"


def test_summary_groups_supplier_quotes_and_exposes_manufacturer_role(db):
    rfq = create_rfq(
        db,
        RFQCreate(cas="50-78-2", name="Aspirin", incoterms=["CIP"]),
        verify=False,
    )
    manufacturer = Supplier(
        company="Acme Chemicals Co., Ltd.",
        company_key="acmechemicals",
        type=SupplierType.MANUFACTURER,
    )
    distributor = Supplier(
        company="Example Trading",
        company_key="exampletrading",
        type=SupplierType.DISTRIBUTOR,
    )
    db.add_all([manufacturer, distributor])
    db.flush()
    manufacturer_manager = Manager(
        supplier_id=manufacturer.id,
        full_name="Acme Sales",
    )
    distributor_manager = Manager(
        supplier_id=distributor.id,
        full_name="Trading Sales",
    )
    db.add_all([manufacturer_manager, distributor_manager])
    db.flush()

    old_quote = create_quotation(
        db,
        QuotationCreate(
            rfq_id=rfq.id,
            manager_id=manufacturer_manager.id,
            price=12.5,
            currency="USD",
        ),
    )
    current_quote = create_quotation(
        db,
        QuotationCreate(
            rfq_id=rfq.id,
            manager_id=manufacturer_manager.id,
            price=11.75,
            currency="USD",
        ),
    )
    distributor_quote = create_quotation(
        db,
        QuotationCreate(
            rfq_id=rfq.id,
            manager_id=distributor_manager.id,
            price=13,
            currency="USD",
        ),
    )

    summary = build_summary(db, rfq.id)
    assert len(summary) == 2
    by_supplier = {row.supplier: row for row in summary}

    manufacturer_row = by_supplier[manufacturer.company]
    assert manufacturer_row.quotation_id == current_quote.id
    assert manufacturer_row.quotation_ids == [old_quote.id, current_quote.id]
    assert manufacturer_row.quotation_count == 2
    assert manufacturer_row.price == 11.75
    assert manufacturer_row.supplier_is_manufacturer is True
    assert manufacturer_row.manufacturer is None

    distributor_row = by_supplier[distributor.company]
    assert distributor_row.quotation_id == distributor_quote.id
    assert distributor_row.quotation_ids == [distributor_quote.id]
    assert distributor_row.quotation_count == 1
    assert distributor_row.supplier_is_manufacturer is False
