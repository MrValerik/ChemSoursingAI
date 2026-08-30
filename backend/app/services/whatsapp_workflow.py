"""Принимает ответы WhatsApp Web в обычный диалог закупочного запроса."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.extraction.llm_client import LLMClient
from app.extraction.parsers import parse_explicit_price_offers
from app.extraction.pipeline import extract_quote
from app.models.communication import Communication
from app.models.document import SupplierDocument
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
from app.services.communication_policy import classify_supplier_message
from app.services.communication_profiles import (
    budget_escalation_note,
    finalize_usage,
    record_policy,
    start_audit,
)
from app.services.document_agent import verify_document
from app.services.document_intake import store_incoming_attachments
from app.services.prompt_service import get_rfq_prompt_context
from app.services.quotation_service import create_quotation

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WhatsAppAcceptResult:
    state: Literal["accepted", "ambiguous", "duplicate", "unmatched"]
    communication_id: int | None = None
    rfq_id: int | None = None

    @property
    def should_process(self) -> bool:
        return self.state == "accepted" and self.communication_id is not None


def normalize_whatsapp_number(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    return digits


def _message_matches_sender(message: Communication, sender: str) -> bool:
    address = (
        message.to_address
        if message.direction == CommDirection.OUTBOUND
        else message.from_address
    )
    manager_number = message.manager.whatsapp if message.manager else None
    return sender in {
        normalize_whatsapp_number(address),
        normalize_whatsapp_number(manager_number),
    }


def _quoted_target(
    db: Session, *, quoted_message_id: str | None, sender: str
) -> Communication | None:
    if not quoted_message_id:
        return None
    exact = db.scalar(
        select(Communication).where(
            Communication.external_id == quoted_message_id,
            Communication.channel == Channel.WHATSAPP,
            Communication.direction == CommDirection.OUTBOUND,
            Communication.rfq_id.is_not(None),
        )
    )
    if exact is not None and _message_matches_sender(exact, sender):
        return exact

    # При отправке нескольких файлов внешний id коммуникации равен id первого
    # файла, а остальные провайдерские id лежат в метаданных вложений.
    candidates = db.scalars(
        select(Communication)
        .where(
            Communication.channel == Channel.WHATSAPP,
            Communication.direction == CommDirection.OUTBOUND,
            Communication.rfq_id.is_not(None),
            Communication.attachments.is_not(None),
        )
        .order_by(Communication.created_at.desc(), Communication.id.desc())
        .limit(500)
    ).all()
    for candidate in candidates:
        if not _message_matches_sender(candidate, sender):
            continue
        if any(
            item.get("provider_message_id") == quoted_message_id
            for item in candidate.attachments or []
        ):
            return candidate
    return None


def _latest_targets(db: Session, sender: str) -> list[Communication]:
    managers = [
        manager
        for manager in db.scalars(
            select(Manager).where(Manager.whatsapp.is_not(None))
        ).all()
        if normalize_whatsapp_number(manager.whatsapp) == sender
    ]
    if not managers:
        return []
    manager_ids = [manager.id for manager in managers]
    messages = db.scalars(
        select(Communication)
        .join(RFQ, RFQ.id == Communication.rfq_id)
        .where(
            Communication.channel == Channel.WHATSAPP,
            Communication.manager_id.in_(manager_ids),
            Communication.rfq_id.is_not(None),
            RFQ.deleted_at.is_(None),
        )
        .order_by(Communication.created_at.desc(), Communication.id.desc())
    ).all()
    latest_by_rfq: dict[int, Communication] = {}
    for message in messages:
        if message.rfq_id is not None and message.rfq_id not in latest_by_rfq:
            latest_by_rfq[message.rfq_id] = message
    return list(latest_by_rfq.values())


def _decode_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gateway_errors = {
        "media_too_large": "Файл WhatsApp превышает допустимые 25 МБ",
        "media_download_failed": "WhatsApp Web не смог загрузить файл",
    }
    decoded: list[dict[str, Any]] = []
    for item in items:
        result: dict[str, Any] = {
            "filename": item.get("filename") or "document",
            "content_type": item.get("content_type") or "application/octet-stream",
            "size": item.get("size") or 0,
        }
        if item.get("error"):
            error = str(item["error"])
            result["error"] = gateway_errors.get(error, error)[:300]
        payload = item.get("content_base64")
        if payload:
            try:
                result["content"] = base64.b64decode(payload, validate=True)
                result["size"] = len(result["content"])
            except (binascii.Error, ValueError):
                result["error"] = "WhatsApp Web передал повреждённое вложение"
        decoded.append(result)
    return decoded


def _message_at(timestamp: int) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return datetime.now(timezone.utc)


def _save_inbound(
    db: Session,
    *,
    message_id: str,
    from_number: str,
    body: str,
    timestamp: int,
    quoted_message_id: str | None,
    attachments: list[dict[str, Any]],
    target: Communication | None,
    status: str,
) -> Communication:
    rfq_id = target.rfq_id if target else None
    manager_id = target.manager_id if target else None
    manager = db.get(Manager, manager_id) if manager_id is not None else None
    display_body = body.strip()
    if not display_body and attachments:
        display_body = "Получено вложение WhatsApp без текстового сообщения."
    inbound = Communication(
        rfq_id=rfq_id,
        manager_id=manager_id,
        direction=CommDirection.INBOUND,
        channel=Channel.WHATSAPP,
        subject=None,
        body=display_body,
        from_address=from_number,
        to_address=get_settings().whatsapp_phone_id or None,
        status=status,
        message_at=_message_at(timestamp),
        thread_id=quoted_message_id or message_id,
        external_id=message_id,
        attachments=None,
    )
    db.add(inbound)
    db.flush()
    inbound.attachments = (
        store_incoming_attachments(
            db,
            rfq_id=rfq_id,
            communication_id=inbound.id,
            supplier_id=manager.supplier_id if manager else None,
            attachments=_decode_attachments(attachments),
        )
        or None
    )
    return inbound


def accept_business_whatsapp(
    db: Session,
    *,
    message_id: str,
    from_number: str,
    body: str,
    timestamp: int,
    quoted_message_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> WhatsAppAcceptResult:
    """Сохраняет ответ в RFQ; неоднозначный номер не запускает извлечение."""
    existing = db.scalar(
        select(Communication).where(Communication.external_id == message_id)
    )
    if existing is not None:
        return WhatsAppAcceptResult("duplicate", existing.id, existing.rfq_id)

    sender = normalize_whatsapp_number(from_number)
    if not sender:
        return WhatsAppAcceptResult("unmatched")
    target = _quoted_target(
        db, quoted_message_id=quoted_message_id, sender=sender
    )
    targets = [target] if target is not None else _latest_targets(db, sender)
    if not targets:
        return WhatsAppAcceptResult("unmatched")

    ambiguous = target is None and len(targets) > 1
    chosen = targets[0]
    try:
        inbound = _save_inbound(
            db,
            message_id=message_id,
            from_number=from_number,
            body=body,
            timestamp=timestamp,
            quoted_message_id=quoted_message_id,
            attachments=attachments or [],
            target=chosen,
            status="received_ambiguous" if ambiguous else "received",
        )
        rfq = db.get(RFQ, inbound.rfq_id)
        if ambiguous and rfq is not None:
            competing = ", ".join(str(item.rfq_id) for item in targets)
            db.add(
                Escalation(
                    rfq_id=rfq.id,
                    communication_id=inbound.id,
                    manager_id=inbound.manager_id,
                    reason=EscalationReason.OTHER,
                    status=EscalationStatus.OPEN,
                    note=(
                        "Один номер WhatsApp связан с несколькими диалогами RFQ "
                        f"({competing}), а поставщик не ответил на конкретное "
                        "сообщение. Реплика показана в последнем диалоге, но "
                        "автоматическое извлечение остановлено до проверки человеком."
                    ),
                )
            )
            rfq.status = RFQStatus.ESCALATED
        elif rfq is not None:
            rfq.status = RFQStatus.COLLECTING
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(Communication).where(Communication.external_id == message_id)
        )
        return WhatsAppAcceptResult(
            "duplicate",
            duplicate.id if duplicate else None,
            duplicate.rfq_id if duplicate else None,
        )
    return WhatsAppAcceptResult(
        "ambiguous" if ambiguous else "accepted", inbound.id, inbound.rfq_id
    )


def store_unmatched_whatsapp(
    db: Session,
    *,
    message_id: str,
    from_number: str,
    body: str,
    timestamp: int,
    quoted_message_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> WhatsAppAcceptResult:
    existing = db.scalar(
        select(Communication).where(Communication.external_id == message_id)
    )
    if existing is not None:
        return WhatsAppAcceptResult("duplicate", existing.id, existing.rfq_id)
    try:
        inbound = _save_inbound(
            db,
            message_id=message_id,
            from_number=from_number,
            body=body,
            timestamp=timestamp,
            quoted_message_id=quoted_message_id,
            attachments=attachments or [],
            target=None,
            status="unresolved",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(Communication).where(Communication.external_id == message_id)
        )
        return WhatsAppAcceptResult(
            "duplicate", duplicate.id if duplicate else None, None
        )
    return WhatsAppAcceptResult("unmatched", inbound.id, None)


def _verify_documents(
    db: Session, *, rfq: RFQ, attachments: list[dict[str, Any]], llm: LLMClient
) -> None:
    for item in attachments:
        document_id = item.get("document_id")
        if not document_id:
            continue
        document = db.get(SupplierDocument, int(document_id))
        if document is None or document.verification is not None:
            continue
        try:
            verify_document(
                db,
                document,
                expected_cas=rfq.cas,
                expected_name=rfq.name,
                llm=llm,
            )
        except Exception:
            logger.exception("Не удалось проверить WhatsApp-документ %s", document_id)
    db.flush()


def _escalate(
    db: Session, *, inbound: Communication, rfq: RFQ, note: str
) -> None:
    db.add(
        Escalation(
            rfq_id=rfq.id,
            communication_id=inbound.id,
            manager_id=inbound.manager_id,
            reason=EscalationReason.OTHER,
            status=EscalationStatus.OPEN,
            note=note,
        )
    )
    rfq.status = RFQStatus.ESCALATED


def escalate_processing_failure(db: Session, *, communication_id: int) -> None:
    """Делает фоновый сбой видимым в том же диалоге, не теряя оригинал."""
    inbound = db.get(Communication, communication_id)
    if inbound is None or inbound.rfq_id is None:
        return
    if db.scalar(
        select(Escalation.id).where(Escalation.communication_id == inbound.id)
    ) is not None:
        return
    rfq = db.get(RFQ, inbound.rfq_id)
    if rfq is None:
        return
    inbound.status = "processing_error"
    _escalate(
        db,
        inbound=inbound,
        rfq=rfq,
        note=(
            "Ответ WhatsApp сохранён, но автоматическая обработка завершилась "
            "ошибкой. Проверьте реплику и ответьте поставщику вручную."
        ),
    )
    db.commit()


def process_business_whatsapp(
    db: Session, *, communication_id: int
) -> int:
    """Классифицирует безопасный ответ и извлекает предложения как из Email."""
    inbound = db.get(Communication, communication_id)
    if (
        inbound is None
        or inbound.channel != Channel.WHATSAPP
        or inbound.direction != CommDirection.INBOUND
        or inbound.rfq_id is None
        or inbound.status != "received"
    ):
        return 0
    rfq = db.get(RFQ, inbound.rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        return 0
    if db.scalar(
        select(Quotation.id).where(
            Quotation.source_communication_id == inbound.id
        )
    ) is not None:
        return 0
    if db.scalar(
        select(Escalation.id).where(Escalation.communication_id == inbound.id)
    ) is not None:
        return 0

    text = (inbound.body or "").strip()
    audit_start = start_audit(
        db,
        event_key=f"whatsapp:{inbound.external_id}",
        text=text,
        rfq_id=rfq.id,
        manager_id=inbound.manager_id,
        communication_id=inbound.id,
        actor_id=rfq.owner_id,
        prompt_kind="extraction",
    )
    if audit_start.duplicate:
        return 0
    if not audit_start.budget.allowed:
        _escalate(
            db,
            inbound=inbound,
            rfq=rfq,
            note=budget_escalation_note(audit_start.audit),
        )
        db.commit()
        return 0

    client = LLMClient()
    policy = classify_supplier_message(
        text,
        rfq_name=rfq.name,
        rfq_cas=rfq.cas,
        llm=client,
    )
    record_policy(audit_start.audit, policy)
    if not policy.auto_reply_allowed:
        finalize_usage(audit_start.audit, client, reply_generated=False)
        _escalate(
            db,
            inbound=inbound,
            rfq=rfq,
            note=(
                "Автоматическая обработка WhatsApp остановлена: "
                f"{policy.explanation} Категория: {policy.category}."
            ),
        )
        db.commit()
        return 0

    system_prompt, instructions = get_rfq_prompt_context(
        db, rfq.id, kind="extraction"
    )
    quote = extract_quote(
        text,
        use_llm=True,
        llm=client,
        system_prompt=system_prompt,
        additional_instructions=instructions,
    )
    stored_attachments = list(inbound.attachments or [])
    attachment_kinds = {
        item.get("kind")
        for item in stored_attachments
        if item.get("document_id") is not None
    }
    _verify_documents(
        db, rfq=rfq, attachments=stored_attachments, llm=client
    )

    explicit_offers = parse_explicit_price_offers(text)
    offer_overrides = explicit_offers if len(explicit_offers) > 1 else [{}]
    created = 0
    for offer in offer_overrides:
        confidence = dict(quote.field_confidence or {})
        for field_name in (
            "price",
            "currency",
            "incoterm",
            "price_unit",
            "quoted_quantity",
        ):
            if offer.get(field_name) is not None:
                confidence[field_name] = 0.95
        create_quotation(
            db,
            QuotationCreate(
                rfq_id=rfq.id,
                manager_id=inbound.manager_id,
                price=offer.get("price", quote.price),
                currency=offer.get("currency", quote.currency),
                incoterm=offer.get("incoterm", quote.incoterm),
                moq=quote.moq,
                grade=quote.grade,
                payment_terms=quote.payment_terms,
                lead_time=quote.lead_time,
                manufacturer=quote.manufacturer,
                origin_country=quote.origin_country,
                packaging=quote.packaging,
                price_unit=offer.get("price_unit", quote.price_unit),
                quoted_quantity=offer.get(
                    "quoted_quantity", quote.quoted_quantity
                ),
                total_price=quote.total_price,
                delivery_cost=quote.delivery_cost,
                duty_cost=quote.duty_cost,
                vat_cost=quote.vat_cost,
                landed_cost=quote.landed_cost,
                cost_currency=quote.cost_currency,
                is_hazmat=quote.is_hazmat,
                has_coa=quote.has_coa or "coa" in attachment_kinds,
                has_tds=quote.has_tds or "tds" in attachment_kinds,
                field_confidence=confidence,
                source_text=text,
            ),
            source_communication_id=inbound.id,
        )
        created += 1
    rfq.status = RFQStatus.PARSED
    finalize_usage(audit_start.audit, client, reply_generated=False)
    db.commit()
    return created
