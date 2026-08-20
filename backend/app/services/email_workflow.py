"""Связывает корпоративную почту с RFQ, котировками и дозапросами."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
    IncomingEmail,
)
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.extraction.pipeline import extract_quote
from app.models.communication import Communication
from app.models.enums import (
    Channel,
    CommDirection,
    EscalationReason,
    EscalationStatus,
    RFQStatus,
)
from app.models.escalation import Escalation
from app.models.manager import Manager
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.schemas.quotation import QuotationCreate
from app.services.completeness import accumulate_quotations
from app.services.communication_policy import classify_supplier_message
from app.services.document_intake import store_incoming_attachments
from app.services.integration_settings import effective_email_settings
from app.services.prompt_service import get_rfq_prompt_context
from app.services.quotation_service import create_quotation

_RFQ_MARKER = re.compile(r"\[RFQ-(\d+)]", re.IGNORECASE)
_MISSING_LABELS = {
    "price": "unit price and currency",
    "currency": "quote currency",
    "incoterm": "delivery basis / Incoterm",
    "moq": "minimum order quantity (MOQ)",
    "grade": "product grade or purity",
    "payment_terms": "payment terms",
    "lead_time": "production and delivery lead time",
    "specification": "CoA or TDS",
}


@dataclass(slots=True)
class EmailSyncSummary:
    fetched: int = 0
    processed: int = 0
    duplicates: int = 0
    unmatched: int = 0
    quotations_created: int = 0
    followups_drafted: int = 0
    followups_sent: int = 0
    escalations_created: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "processed": self.processed,
            "duplicates": self.duplicates,
            "unmatched": self.unmatched,
            "quotations_created": self.quotations_created,
            "followups_drafted": self.followups_drafted,
            "followups_sent": self.followups_sent,
            "escalations_created": self.escalations_created,
            "errors": self.errors,
        }


def _find_rfq(db: Session, message: IncomingEmail) -> RFQ | None:
    match = _RFQ_MARKER.search(message.subject)
    if match:
        rfq = db.get(RFQ, int(match.group(1)))
        if rfq is not None and rfq.deleted_at is None:
            return rfq
    for reference in [message.in_reply_to, *message.references]:
        if not reference:
            continue
        communication = db.scalar(
            select(Communication).where(Communication.external_id == reference)
        )
        if communication and communication.rfq_id:
            rfq = db.get(RFQ, communication.rfq_id)
            return rfq if rfq is not None and rfq.deleted_at is None else None
    return None


def _find_manager(db: Session, address: str) -> Manager | None:
    if not address:
        return None
    return db.scalar(
        select(Manager)
        .where(func.lower(Manager.email) == address.strip().lower())
        .order_by(Manager.id)
        .limit(1)
    )


def _supplier_manager_ids(db: Session, manager: Manager | None) -> list[int]:
    if manager is None:
        return []
    return list(
        db.scalars(
            select(Manager.id).where(Manager.supplier_id == manager.supplier_id)
        ).all()
    )


def _supplier_quotations(db: Session, rfq_id: int, manager: Manager | None):
    if manager is None:
        return []
    manager_ids = _supplier_manager_ids(db, manager)
    return list(
        db.scalars(
            select(Quotation)
            .where(
                Quotation.rfq_id == rfq_id,
                Quotation.manager_id.in_(manager_ids),
            )
            .order_by(Quotation.created_at, Quotation.id)
        ).all()
    )


def _cancel_pending_followups(
    db: Session, *, rfq_id: int, manager: Manager | None
) -> None:
    """Не даёт оператору отправить устаревший дозапрос после сбора данных."""
    if manager is None:
        return
    manager_ids = _supplier_manager_ids(db, manager)
    drafts = db.scalars(
        select(Communication).where(
            Communication.rfq_id == rfq_id,
            Communication.manager_id.in_(manager_ids),
            Communication.direction == CommDirection.OUTBOUND,
            Communication.channel == Channel.EMAIL,
            Communication.status == "draft",
            Communication.idempotency_key.is_(None),
            Communication.thread_id.is_not(None),
        )
    ).all()
    for draft in drafts:
        draft.status = "cancelled"
    if drafts:
        db.commit()


def _subject_label(rfq: RFQ) -> str:
    """Предмет запроса для письма: с номером, если он есть.

    У запроса по спецификации номера нет, и «CAS None» в письме
    поставщику выглядит как ошибка системы.
    """
    return f"{rfq.name} (CAS {rfq.cas})" if rfq.cas else rfq.name


def _fallback_followup(rfq: RFQ, missing: list[str]) -> str:
    fields = ", ".join(_MISSING_LABELS.get(item, item) for item in missing)
    return (
        "Dear Supplier,\n\n"
        f"Thank you for your reply regarding {_subject_label(rfq)}. "
        f"To complete our evaluation, could you please provide: {fields}?\n\n"
        "Please keep the previously requested product, grade and delivery "
        "requirements unchanged.\n\nBest regards,\nProcurement Department"
    )


def _render_followup(db: Session, rfq: RFQ, missing: list[str]) -> str:
    fallback = _fallback_followup(rfq, missing)
    system_prompt, saved_instructions = get_rfq_prompt_context(
        db, rfq.id, kind="followup"
    )
    if not system_prompt:
        return fallback
    try:
        return LLMClient().generate_text(
            system_prompt=system_prompt,
            user_text=(
                f"RFQ: {_subject_label(rfq)}.\n"
                f"Недостающие данные: {', '.join(missing)}."
            ),
            additional_instructions=(
                "Подготовь только готовое письмо поставщику на английском языке. "
                "Не добавляй новые требования. "
                + (saved_instructions or "")
            ),
            max_tokens=256,
        )
    except LLMUnavailableError:
        return fallback


def _create_followup(
    db: Session,
    *,
    rfq: RFQ,
    incoming: IncomingEmail,
    manager: Manager | None,
    missing: list[str],
    connector: EmailConnector,
) -> str | None:
    runtime = getattr(connector, "settings", None) or effective_email_settings(db)[0]
    mode = runtime.auto_followup_mode.strip().lower()
    if mode == "off" or not missing:
        return None
    body = _render_followup(db, rfq, missing)
    subject = (
        incoming.subject
        if incoming.subject.lower().startswith("re:")
        else f"Re: {incoming.subject}"
    )
    status = "draft"
    external_id = None
    if mode == "send" and runtime.email_delivery_mode == "live":
        try:
            external_id = connector.send(
                to_address=incoming.from_address,
                subject=subject,
                body=body,
                in_reply_to=incoming.message_id,
                references=[*incoming.references, incoming.message_id],
            )
            status = "sent"
        except (EmailConfigurationError, EmailDeliveryError):
            # Потерять дозапрос хуже, чем оставить его оператору как черновик.
            status = "draft"
    db.add(
        Communication(
            rfq_id=rfq.id,
            manager_id=manager.id if manager else None,
            direction=CommDirection.OUTBOUND,
            channel=Channel.EMAIL,
            subject=subject,
            body=body,
            from_address=runtime.email_from or None,
            to_address=incoming.from_address,
            status=status,
            # Для ручной отправки черновика отвечаем именно на входящее письмо.
            thread_id=incoming.message_id,
            external_id=external_id,
            attachments=None,
        )
    )
    db.commit()
    return status


def sync_inbox(
    db: Session,
    connector: EmailConnector | None = None,
    *,
    limit: int = 20,
) -> EmailSyncSummary:
    """Загружает новые письма и создаёт котировки один раз по Message-ID."""
    email = connector or EmailConnector(effective_email_settings(db)[0])
    messages = email.fetch_unseen(limit=limit)
    summary = EmailSyncSummary(fetched=len(messages))
    seen_uids: list[str] = []

    for message in messages:
        try:
            duplicate = db.scalar(
                select(Communication.id).where(
                    Communication.external_id == message.message_id
                )
            )
            if duplicate is not None:
                summary.duplicates += 1
                seen_uids.append(message.uid)
                continue
            rfq = _find_rfq(db, message)
            if rfq is None:
                summary.unmatched += 1
                continue
            manager = _find_manager(db, message.from_address)
            inbound = Communication(
                rfq_id=rfq.id,
                manager_id=manager.id if manager else None,
                direction=CommDirection.INBOUND,
                channel=Channel.EMAIL,
                subject=message.subject,
                body=message.text,
                from_address=message.from_address,
                to_address=", ".join(message.to_addresses) or None,
                status="received",
                thread_id=message.in_reply_to or message.message_id,
                external_id=message.message_id,
                attachments=None,
            )
            db.add(inbound)
            rfq.status = RFQStatus.COLLECTING
            db.flush()
            stored_attachments = store_incoming_attachments(
                db,
                rfq_id=rfq.id,
                communication_id=inbound.id,
                attachments=message.attachments,
            )
            inbound.attachments = stored_attachments or None

            policy = classify_supplier_message(
                message.text,
                rfq_name=rfq.name,
                rfq_cas=rfq.cas,
            )
            if not policy.auto_reply_allowed:
                db.add(
                    Escalation(
                        rfq_id=rfq.id,
                        communication_id=inbound.id,
                        manager_id=manager.id if manager else None,
                        reason=EscalationReason.OTHER,
                        status=EscalationStatus.OPEN,
                        note=(
                            "Автоответ остановлен: "
                            f"{policy.explanation} "
                            f"Категория: {policy.category}."
                        ),
                    )
                )
                rfq.status = RFQStatus.ESCALATED
                db.commit()
                summary.escalations_created += 1
                summary.processed += 1
                seen_uids.append(message.uid)
                continue

            system_prompt, instructions = get_rfq_prompt_context(
                db, rfq.id, kind="extraction"
            )
            quote = extract_quote(
                message.text,
                use_llm=True,
                system_prompt=system_prompt,
                additional_instructions=instructions,
            )
            attachment_kinds = {
                item.get("kind")
                for item in stored_attachments
                if item.get("document_id") is not None
            }
            quotation = create_quotation(
                db,
                QuotationCreate(
                    rfq_id=rfq.id,
                    manager_id=manager.id if manager else None,
                    price=quote.price,
                    currency=quote.currency,
                    incoterm=quote.incoterm,
                    moq=quote.moq,
                    grade=quote.grade,
                    payment_terms=quote.payment_terms,
                    lead_time=quote.lead_time,
                    has_coa=quote.has_coa or "coa" in attachment_kinds,
                    has_tds=quote.has_tds or "tds" in attachment_kinds,
                    field_confidence=quote.field_confidence,
                    source_text=message.text,
                ),
            )
            supplier_quotations = _supplier_quotations(db, rfq.id, manager)
            progress = accumulate_quotations(
                supplier_quotations if supplier_quotations else [quotation]
            )
            rfq.status = RFQStatus.PARSED
            db.commit()
            summary.quotations_created += 1
            if progress.completeness.is_complete:
                _cancel_pending_followups(
                    db,
                    rfq_id=rfq.id,
                    manager=manager,
                )
            followup_status = _create_followup(
                db,
                rfq=rfq,
                incoming=message,
                manager=manager,
                missing=list(
                    dict.fromkeys(
                        [
                            *progress.completeness.missing_fields,
                            *progress.completeness.low_confidence_fields,
                        ]
                    )
                ),
                connector=email,
            )
            if followup_status == "draft":
                summary.followups_drafted += 1
            elif followup_status == "sent":
                summary.followups_sent += 1
            summary.processed += 1
            seen_uids.append(message.uid)
        except Exception as exc:
            db.rollback()
            summary.errors.append(
                f"{message.message_id}: {type(exc).__name__}: {exc}"
            )

    if seen_uids:
        try:
            email.mark_seen(seen_uids)
        except Exception as exc:
            summary.errors.append(str(exc))
    return summary
