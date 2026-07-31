"""Прикладной сервис котировок (L2): создание с контролем полноты,
сводная таблица по RFQ, авто-эскалация (функции 6, 7, 9 ТЗ)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.escalation import Escalation
from app.models.enums import EscalationStatus, RFQStatus
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.schemas.quotation import QuotationCreate, SummaryRow
from app.services.completeness import evaluate_completeness
from app.services.escalation_rules import detect_escalation


def create_quotation(db: Session, data: QuotationCreate) -> Quotation:
    """Сохраняет котировку, вычисляет полноту и при необходимости заводит
    эскалацию специалисту."""
    quote_dict = {
        "price": data.price,
        "incoterm": data.incoterm,
        "moq": data.moq,
        "grade": data.grade,
        "payment_terms": data.payment_terms,
        "lead_time": data.lead_time,
        "has_coa": data.has_coa,
        "has_tds": data.has_tds,
    }
    completeness = evaluate_completeness(quote_dict, data.field_confidence)

    quotation = Quotation(
        rfq_id=data.rfq_id,
        manager_id=data.manager_id,
        price=data.price,
        currency=data.currency,
        incoterm=data.incoterm,
        moq=data.moq,
        grade=data.grade,
        payment_terms=data.payment_terms,
        lead_time=data.lead_time,
        has_coa=data.has_coa,
        has_tds=data.has_tds,
        is_complete=completeness.is_complete,
        field_confidence=data.field_confidence,
    )
    db.add(quotation)

    # Авто-эскалация нестандартного кейса.
    reason = detect_escalation(quote_dict, completeness, free_text=data.source_text)
    if reason is not None:
        db.add(
            Escalation(
                rfq_id=data.rfq_id,
                reason=reason,
                status=EscalationStatus.OPEN,
                note=f"Auto-escalated: {reason.value}",
            )
        )

    db.commit()
    db.refresh(quotation)
    return quotation


def build_summary(db: Session, rfq_id: int) -> list[SummaryRow]:
    """Сводная сравнительная таблица по RFQ: полные котировки — выше."""
    stmt = select(Quotation).where(Quotation.rfq_id == rfq_id)
    rows: list[SummaryRow] = []
    for q in db.scalars(stmt).all():
        manager = q.manager
        supplier = manager.supplier.company if manager and manager.supplier else None
        rows.append(
            SummaryRow(
                quotation_id=q.id,
                supplier=supplier,
                manager=manager.full_name if manager else None,
                price=float(q.price) if q.price is not None else None,
                currency=q.currency,
                incoterm=q.incoterm,
                moq=q.moq,
                grade=q.grade,
                lead_time=q.lead_time,
                has_coa=q.has_coa,
                has_tds=q.has_tds,
                is_complete=q.is_complete,
            )
        )

    # Сортировка: сначала полные, затем по возрастанию цены (None — в конец).
    rows.sort(key=lambda r: (not r.is_complete, r.price is None, r.price or 0))

    # Перевод статуса RFQ в SUMMARIZED, если есть хоть одна котировка.
    if rows:
        rfq = db.get(RFQ, rfq_id)
        if rfq and rfq.status in (RFQStatus.SENT, RFQStatus.COLLECTING, RFQStatus.PARSED):
            rfq.status = RFQStatus.SUMMARIZED
            db.commit()
    return rows
