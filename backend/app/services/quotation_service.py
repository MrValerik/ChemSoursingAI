"""Прикладной сервис котировок (L2): создание с контролем полноты,
сводная таблица по RFQ, авто-эскалация (функции 6, 7, 9 ТЗ)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.communication import Communication
from app.models.escalation import Escalation
from app.models.integration import CommunicationTestRun
from app.models.enums import EscalationStatus, RFQStatus, SupplierType
from app.models.purchase_decision import PurchaseDecision
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.models.user import User
from app.schemas.quotation import QuotationCreate, SummaryRow
from app.services.completeness import evaluate_completeness
from app.services.escalation_rules import detect_escalation


def create_quotation(db: Session, data: QuotationCreate) -> Quotation:
    """Сохраняет котировку, вычисляет полноту и при необходимости заводит
    эскалацию специалисту."""
    quote_dict = {
        "price": data.price,
        "currency": data.currency,
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
        manufacturer=data.manufacturer,
        origin_country=data.origin_country,
        packaging=data.packaging,
        price_unit=data.price_unit,
        quoted_quantity=data.quoted_quantity,
        total_price=data.total_price,
        delivery_cost=data.delivery_cost,
        duty_cost=data.duty_cost,
        vat_cost=data.vat_cost,
        landed_cost=data.landed_cost,
        cost_currency=data.cost_currency,
        is_hazmat=data.is_hazmat,
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
    test_run_by_quotation_id = {
        run.quotation_id: run.id
        for run in db.scalars(
            select(CommunicationTestRun).where(
                CommunicationTestRun.rfq_id == rfq_id,
                CommunicationTestRun.quotation_id.is_not(None),
            )
        ).all()
        if run.quotation_id is not None
    }
    latest_channel_by_manager: dict[int, str] = {}
    for communication in db.scalars(
        select(Communication)
        .where(
            Communication.rfq_id == rfq_id,
            Communication.manager_id.is_not(None),
        )
        .order_by(Communication.created_at, Communication.id)
    ).all():
        if communication.manager_id is not None:
            latest_channel_by_manager[communication.manager_id] = (
                communication.channel.value
            )
    rows: list[SummaryRow] = []
    for q in db.scalars(stmt).all():
        manager = q.manager
        supplier_row = manager.supplier if manager else None
        supplier = supplier_row.company if supplier_row else None
        manufacturer = q.manufacturer
        if (
            not manufacturer
            and supplier_row is not None
            and supplier_row.type == SupplierType.MANUFACTURER
        ):
            manufacturer = supplier_row.company
        rows.append(
            SummaryRow(
                quotation_id=q.id,
                supplier_id=manager.supplier_id if manager else None,
                manager_id=q.manager_id,
                test_run_id=test_run_by_quotation_id.get(q.id),
                conversation_channel=(
                    latest_channel_by_manager.get(q.manager_id)
                    if q.manager_id is not None
                    else None
                ),
                supplier=(
                    supplier
                    or (
                        "Тестовый поставщик"
                        if q.id in test_run_by_quotation_id
                        else None
                    )
                ),
                manager=manager.full_name if manager else None,
                price=float(q.price) if q.price is not None else None,
                currency=q.currency,
                incoterm=q.incoterm,
                moq=q.moq,
                grade=q.grade,
                payment_terms=q.payment_terms,
                lead_time=q.lead_time,
                manufacturer=manufacturer,
                origin_country=q.origin_country or (
                    supplier_row.country if supplier_row else None
                ),
                packaging=q.packaging,
                price_unit=q.price_unit,
                quoted_quantity=q.quoted_quantity,
                total_price=(
                    float(q.total_price) if q.total_price is not None else None
                ),
                delivery_cost=(
                    float(q.delivery_cost) if q.delivery_cost is not None else None
                ),
                duty_cost=(
                    float(q.duty_cost) if q.duty_cost is not None else None
                ),
                vat_cost=float(q.vat_cost) if q.vat_cost is not None else None,
                landed_cost=(
                    float(q.landed_cost) if q.landed_cost is not None else None
                ),
                cost_currency=q.cost_currency or q.currency,
                is_hazmat=q.is_hazmat,
                has_coa=q.has_coa,
                has_tds=q.has_tds,
                is_complete=q.is_complete,
                field_confidence=q.field_confidence,
                created_at=q.created_at,
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


def save_purchase_decision(
    db: Session,
    *,
    rfq: RFQ,
    quotation_id: int,
    note: str | None,
    actor: User,
) -> PurchaseDecision:
    """Сохраняет выбор человека без отправки заказа или внешнего действия."""
    quotation = db.get(Quotation, quotation_id)
    if quotation is None or quotation.rfq_id != rfq.id:
        raise ValueError("Предложение не относится к этому запросу")

    decision = db.scalar(
        select(PurchaseDecision).where(PurchaseDecision.rfq_id == rfq.id)
    )
    if decision is None:
        decision = PurchaseDecision(rfq_id=rfq.id, quotation_id=quotation.id)
        db.add(decision)
    decision.quotation_id = quotation.id
    decision.selected_by_id = actor.id
    decision.note = note
    db.commit()
    db.refresh(decision)
    return decision
