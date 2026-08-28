"""Идемпотентная сверка старых Email-котировок с сохранёнными письмами."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extraction.email_text import latest_reply_text
from app.extraction.parsers import parse_explicit_price_offers
from app.extraction.pipeline import extract_quote, validate_lead_time_value
from app.models import Communication, Quotation, RFQ, SupplierDocument
from app.models.enums import Channel, CommDirection
from app.services.completeness import evaluate_completeness
from app.services.document_agent import verify_document


@dataclass(slots=True)
class ReconciliationResult:
    communications_checked: int = 0
    quotations_updated: int = 0
    quotations_created: int = 0
    documents_verified: int = 0


_DETERMINISTIC_FIELDS = (
    "price",
    "currency",
    "incoterm",
    "moq",
    "grade",
    "payment_terms",
    "lead_time",
    "manufacturer",
    "origin_country",
    "packaging",
    "price_unit",
    "quoted_quantity",
    "total_price",
    "delivery_cost",
    "duty_cost",
    "vat_cost",
    "landed_cost",
    "cost_currency",
    "is_hazmat",
)


def _apply_rules(
    quotation: Quotation,
    *,
    rules,
    offer: dict[str, object] | None,
    manager_id: int | None,
    has_coa: bool,
    has_tds: bool,
) -> None:
    confidence = dict(quotation.field_confidence or {})
    for field_name in _DETERMINISTIC_FIELDS:
        value = getattr(rules, field_name)
        if value is None or value == "":
            continue
        setattr(quotation, field_name, value)
        if field_name in rules.field_confidence:
            confidence[field_name] = rules.field_confidence[field_name]
    for field_name, value in (offer or {}).items():
        if value is None:
            continue
        setattr(quotation, field_name, value)
        confidence[field_name] = 0.95

    # Старый structured output мог принять наличие товара за срок.
    if validate_lead_time_value(quotation.lead_time) is None:
        quotation.lead_time = None
        confidence.pop("lead_time", None)
    quotation.manager_id = manager_id or quotation.manager_id
    quotation.has_coa = has_coa
    quotation.has_tds = has_tds
    if not has_coa:
        confidence.pop("has_coa", None)
    if not has_tds:
        confidence.pop("has_tds", None)
    quotation.field_confidence = confidence or None
    quotation.is_complete = evaluate_completeness(
        {
            "price": quotation.price,
            "currency": quotation.currency,
            "incoterm": quotation.incoterm,
            "moq": quotation.moq,
            "grade": quotation.grade,
            "payment_terms": quotation.payment_terms,
            "lead_time": quotation.lead_time,
            "has_coa": quotation.has_coa,
            "has_tds": quotation.has_tds,
        },
        quotation.field_confidence,
    ).is_complete


def reconcile_email_quotations(
    db: Session,
    *,
    rfq_id: int | None = None,
    email_address: str | None = None,
    verify_documents: bool = False,
) -> ReconciliationResult:
    """Сверяет связанные котировки, сохраняя исходные письма и их аудит."""
    stmt = select(Communication).where(
        Communication.direction == CommDirection.INBOUND,
        Communication.channel == Channel.EMAIL,
        Communication.rfq_id.is_not(None),
        Communication.manager_id.is_not(None),
    )
    if rfq_id is not None:
        stmt = stmt.where(Communication.rfq_id == rfq_id)
    if email_address:
        stmt = stmt.where(
            func.lower(Communication.from_address) == email_address.casefold()
        )
    communications = list(db.scalars(stmt.order_by(Communication.id)).all())
    result = ReconciliationResult()
    checked_communication_ids: list[int] = []
    for communication in communications:
        quotations = list(
            db.scalars(
                select(Quotation)
                .where(Quotation.source_communication_id == communication.id)
                .order_by(Quotation.id)
            ).all()
        )
        if not quotations:
            continue
        checked_communication_ids.append(communication.id)
        result.communications_checked += 1
        latest = latest_reply_text(communication.body or "")
        rules = extract_quote(latest, use_llm=False)
        offers = parse_explicit_price_offers(latest)
        offer_rows = offers if len(offers) > 1 else [None]
        document_kinds = set(
            db.scalars(
                select(SupplierDocument.kind).where(
                    SupplierDocument.communication_id == communication.id,
                    SupplierDocument.text_status.in_(("extracted", "needs_ocr")),
                )
            ).all()
        )
        has_coa = bool(rules.has_coa or "coa" in document_kinds)
        has_tds = bool(rules.has_tds or "tds" in document_kinds)

        while len(quotations) < len(offer_rows):
            quotation = Quotation(
                rfq_id=communication.rfq_id,
                manager_id=communication.manager_id,
                source_communication_id=communication.id,
                is_complete=False,
                created_at=communication.created_at,
                updated_at=communication.created_at,
            )
            db.add(quotation)
            db.flush()
            quotations.append(quotation)
            result.quotations_created += 1

        for quotation, offer in zip(quotations, offer_rows, strict=False):
            _apply_rules(
                quotation,
                rules=rules,
                offer=offer,
                manager_id=communication.manager_id,
                has_coa=has_coa,
                has_tds=has_tds,
            )
            result.quotations_updated += 1
    if verify_documents:
        document_stmt = select(SupplierDocument).where(
            SupplierDocument.verification.is_(None),
            SupplierDocument.rfq_id.is_not(None),
        )
        if rfq_id is not None:
            document_stmt = document_stmt.where(
                SupplierDocument.rfq_id == rfq_id
            )
        if email_address:
            if not checked_communication_ids:
                db.commit()
                return result
            document_stmt = document_stmt.where(
                SupplierDocument.communication_id.in_(checked_communication_ids)
            )
        documents = db.scalars(
            document_stmt.order_by(SupplierDocument.id)
        ).all()
        for document in documents:
            rfq = db.get(RFQ, document.rfq_id)
            if rfq is None:
                continue
            verify_document(
                db,
                document,
                expected_cas=rfq.cas,
                expected_name=rfq.name,
            )
            result.documents_verified += 1
    db.commit()
    return result
