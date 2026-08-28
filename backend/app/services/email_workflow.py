"""Связывает корпоративную почту с RFQ, котировками и дозапросами."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
    IncomingEmail,
)
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.extraction.email_text import latest_reply_text
from app.extraction.parsers import parse_explicit_price_offers
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
from app.models.document import SupplierDocument
from app.schemas.quotation import QuotationCreate
from app.services.completeness import accumulate_quotations
from app.services.communication_policy import classify_supplier_message
from app.services.communication_profiles import (
    budget_escalation_note,
    finalize_usage,
    handoff_message,
    profile_goal_reached,
    profile_prompt_instructions,
    record_policy,
    start_audit,
)
from app.services.document_intake import store_incoming_attachments
from app.services.document_agent import verify_document
from app.services.email_identity import (
    SenderResolution,
    link_address_history,
    reconcile_unlinked_email_contacts,
    resolve_sender_manager,
)
from app.services.integration_settings import effective_email_settings
from app.services.prompt_service import get_rfq_prompt_context
from app.services.quotation_service import create_quotation
from app.services.rfq_service import external_rfq_name

_RFQ_MARKER = re.compile(r"\[RFQ-(\d+)]", re.IGNORECASE)
logger = logging.getLogger(__name__)
_MISSING_LABELS = {
    "price": "unit price and currency",
    "currency": "quote currency",
    "incoterm": "delivery basis / Incoterm",
    "moq": "minimum order quantity (MOQ)",
    "grade": "product grade or purity",
    "payment_terms": "payment terms",
    "lead_time": "production and delivery lead time",
    "specification": "CoA or TDS",
    "requested_quantity_price": "unit price for the originally requested quantity",
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
    contacts_linked: int = 0
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
            "contacts_linked": self.contacts_linked,
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
    name = external_rfq_name(rfq)
    return f"{name} (CAS {rfq.cas})" if rfq.cas else name


def _fallback_followup(rfq: RFQ, missing: list[str]) -> str:
    labels = []
    for item in missing:
        label = _MISSING_LABELS.get(item, item)
        if item == "requested_quantity_price" and rfq.volume:
            label = f"unit price applicable to the requested quantity of {rfq.volume}"
        labels.append(label)
    fields = ", ".join(labels)
    return (
        "Dear Supplier,\n\n"
        f"Thank you for your reply regarding {_subject_label(rfq)}. "
        f"To complete our evaluation, could you please provide: {fields}?\n\n"
        "Please keep the previously requested product, grade and delivery "
        "requirements unchanged.\n\nBest regards,\nProcurement Department"
    )


def _render_followup(
    db: Session,
    rfq: RFQ,
    missing: list[str],
    *,
    llm: LLMClient | None = None,
    profile_instructions: str = "",
) -> str:
    fallback = _fallback_followup(rfq, missing)
    system_prompt, saved_instructions = get_rfq_prompt_context(
        db, rfq.id, kind="followup"
    )
    if not system_prompt:
        return fallback
    try:
        generated = (llm or LLMClient()).generate_text(
            system_prompt=system_prompt,
            user_text=(
                f"RFQ: {_subject_label(rfq)}.\n"
                f"Недостающие данные: {', '.join(missing)}."
            ),
            additional_instructions=(
                "Подготовь только готовое письмо поставщику на английском языке. "
                "Не добавляй новые требования. "
                "Запрашивай только перечисленные недостающие данные и не "
                "повторяй уже полученные условия. Письмо обязательно должно "
                "содержать вежливое обращение, благодарность и подпись. "
                + (saved_instructions or "")
                + (f"\n\n{profile_instructions}" if profile_instructions else "")
            ),
            max_tokens=256,
        )
        normalized = generated.casefold()
        if not (
            re.search(r"\b(?:dear|hello)\b", normalized)
            and "thank" in normalized
            and re.search(r"\b(?:best|kind) regards\b", normalized)
        ):
            return fallback
        return generated.strip()
    except LLMUnavailableError:
        return fallback


def _quantity_signature(value: str | None) -> tuple[float, str] | None:
    match = re.search(
        r"(?i)\b(\d+(?:\.\d+)?)\s*(kg|g|mt|ton|tonne|l|lb)\b",
        value or "",
    )
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2).casefold()
    if unit in {"ton", "tonne", "mt"}:
        return amount * 1000, "kg"
    if unit == "g":
        return amount / 1000, "kg"
    return amount, unit


def _price_scope_needs_confirmation(rfq: RFQ, quote) -> bool:
    requested = _quantity_signature(rfq.volume)
    quoted = _quantity_signature(quote.quoted_quantity)
    return bool(
        quote.price is not None
        and requested
        and quoted
        and requested != quoted
    )


def _verify_stored_documents(
    db: Session,
    *,
    rfq: RFQ,
    stored_attachments: list[dict],
    llm: LLMClient,
) -> None:
    """Проверяет извлечённые документы, не блокируя обработку самого письма."""
    for item in stored_attachments:
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
            logger.exception("Не удалось проверить документ %s", document_id)
    db.flush()


def _create_followup(
    db: Session,
    *,
    rfq: RFQ,
    incoming: IncomingEmail,
    manager: Manager | None,
    missing: list[str],
    connector: EmailConnector,
    llm: LLMClient | None = None,
    profile_instructions: str = "",
    body_override: str | None = None,
) -> str | None:
    runtime = getattr(connector, "settings", None) or effective_email_settings(db)[0]
    mode = runtime.auto_followup_mode.strip().lower()
    if mode == "off" or (not missing and body_override is None):
        return None
    if body_override is not None:
        body = body_override
    elif mode == "send":
        # Автоматическая внешняя отправка использует только детерминированный
        # текст из сохранённого RFQ. LLM-черновик остаётся доступен оператору,
        # но не может подменить вещество или CAS в письме без подтверждения.
        body = _fallback_followup(rfq, missing)
    else:
        body = _render_followup(
            db,
            rfq,
            missing,
            llm=llm,
            profile_instructions=profile_instructions,
        )
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


def _record_sender_resolution(
    audit,
    resolution: SenderResolution,
    *,
    message: IncomingEmail,
) -> None:
    snapshot = dict(audit.budget_snapshot or {})
    identity_payload = resolution.audit_payload()
    identity_payload["sender_display_name"] = message.from_name
    snapshot["sender_identity"] = identity_payload
    audit.budget_snapshot = snapshot


def _unresolved_sender_note(resolution: SenderResolution) -> str:
    return (
        "Отправитель первого письма не сопоставлен с ранее выбранным "
        "поставщиком. Автоматический ответ остановлен, чтобы не объединить "
        f"чужие переписки. Причина: {resolution.explanation}"
    )


def sync_inbox(
    db: Session,
    connector: EmailConnector | None = None,
    *,
    limit: int = 20,
) -> EmailSyncSummary:
    """Загружает новые письма и создаёт котировки один раз по Message-ID."""
    email = connector or EmailConnector(effective_email_settings(db)[0])
    summary = EmailSyncSummary()
    try:
        summary.contacts_linked = reconcile_unlinked_email_contacts(db)
        if summary.contacts_linked:
            db.commit()
    except Exception as exc:
        db.rollback()
        summary.errors.append(
            f"Повторная привязка контактов: {type(exc).__name__}: {exc}"
        )
    messages = email.fetch_unseen(limit=limit)
    summary.fetched = len(messages)
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
            resolution = resolve_sender_manager(
                db,
                rfq=rfq,
                message=message,
                allow_ai=False,
            )
            manager = resolution.manager
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
                supplier_id=manager.supplier_id if manager else None,
                attachments=message.attachments,
            )
            inbound.attachments = stored_attachments or None

            audit_start = start_audit(
                db,
                event_key=f"email:{message.message_id}",
                text=message.text,
                rfq_id=rfq.id,
                manager_id=manager.id if manager else None,
                communication_id=inbound.id,
                actor_id=rfq.owner_id,
                prompt_kind="extraction",
            )
            _record_sender_resolution(
                audit_start.audit,
                resolution,
                message=message,
            )
            if not audit_start.budget.allowed:
                db.add(
                    Escalation(
                        rfq_id=rfq.id,
                        communication_id=inbound.id,
                        manager_id=manager.id if manager else None,
                        reason=EscalationReason.OTHER,
                        status=EscalationStatus.OPEN,
                        note=budget_escalation_note(audit_start.audit),
                    )
                )
                rfq.status = RFQStatus.ESCALATED
                db.commit()
                summary.escalations_created += 1
                summary.processed += 1
                seen_uids.append(message.uid)
                continue

            client = LLMClient()

            if manager is None:
                resolution = resolve_sender_manager(
                    db,
                    rfq=rfq,
                    message=message,
                    llm=client,
                    allow_ai=True,
                )
                manager = resolution.manager
                _record_sender_resolution(
                    audit_start.audit,
                    resolution,
                    message=message,
                )
                if manager is not None:
                    audit_start.audit.manager_id = manager.id
                    link_address_history(
                        db,
                        rfq_id=rfq.id,
                        address=message.from_address,
                        resolution=resolution,
                    )
                    inbound.manager_id = manager.id
                    summary.contacts_linked += 1
                else:
                    audit_start.audit.policy_route = "escalate"
                    audit_start.audit.policy_category = "sender_identity_unknown"
                    audit_start.audit.policy_explanation = resolution.explanation
                    audit_start.audit.policy_method = resolution.method
                    finalize_usage(
                        audit_start.audit,
                        client,
                        reply_generated=False,
                    )
                    db.add(
                        Escalation(
                            rfq_id=rfq.id,
                            communication_id=inbound.id,
                            manager_id=None,
                            reason=EscalationReason.OTHER,
                            status=EscalationStatus.OPEN,
                            note=_unresolved_sender_note(resolution),
                        )
                    )
                    rfq.status = RFQStatus.ESCALATED
                    db.commit()
                    summary.escalations_created += 1
                    summary.processed += 1
                    seen_uids.append(message.uid)
                    continue

            interpretation_text = latest_reply_text(message.text)
            policy = classify_supplier_message(
                interpretation_text,
                rfq_name=rfq.name,
                rfq_cas=rfq.cas,
                llm=client,
            )
            record_policy(audit_start.audit, policy)
            if not policy.auto_reply_allowed:
                finalize_usage(audit_start.audit, client, reply_generated=False)
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
                interpretation_text,
                use_llm=True,
                llm=client,
                system_prompt=system_prompt,
                additional_instructions=instructions,
            )
            attachment_kinds = {
                item.get("kind")
                for item in stored_attachments
                if item.get("document_id") is not None
            }
            _verify_stored_documents(
                db,
                rfq=rfq,
                stored_attachments=stored_attachments,
                llm=client,
            )

            explicit_offers = parse_explicit_price_offers(interpretation_text)
            offer_overrides = explicit_offers if len(explicit_offers) > 1 else [{}]
            created_quotations: list[Quotation] = []
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
                created_quotations.append(
                    create_quotation(
                        db,
                        QuotationCreate(
                            rfq_id=rfq.id,
                            manager_id=manager.id if manager else None,
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
                            source_text=interpretation_text,
                        ),
                        source_communication_id=inbound.id,
                    )
                )
            supplier_quotations = _supplier_quotations(db, rfq.id, manager)
            progress = accumulate_quotations(
                supplier_quotations if supplier_quotations else created_quotations
            )
            rfq.status = RFQStatus.PARSED
            db.commit()
            summary.quotations_created += len(created_quotations)
            if progress.completeness.is_complete:
                _cancel_pending_followups(
                    db,
                    rfq_id=rfq.id,
                    manager=manager,
                )
            profile_complete = profile_goal_reached(
                audit_start.profile,
                progress.quote,
            )
            handoff = (
                handoff_message(audit_start.profile)
                if audit_start.profile.slug == "chemist" and profile_complete
                else None
            )
            missing_fields = list(
                dict.fromkeys(
                    [
                        *progress.completeness.missing_fields,
                        *progress.completeness.low_confidence_fields,
                    ]
                )
            )
            profile_missing = [
                field
                for field in missing_fields
                if field in set(audit_start.profile.required_fields or [])
            ]
            # Новая цена без собственного базиса остаётся несопоставимым
            # вариантом, даже если в старом письме поставщик давал другой
            # Incoterm. Не закрываем это поле накопленным значением.
            if (
                audit_start.profile.slug == "buyer"
                and quote.price is not None
                and quote.incoterm is None
            ):
                profile_missing.append("incoterm")
            if (
                audit_start.profile.slug == "buyer"
                and _price_scope_needs_confirmation(rfq, quote)
            ):
                profile_missing.append("requested_quantity_price")
            profile_missing = list(dict.fromkeys(profile_missing))
            followup_status = _create_followup(
                db,
                rfq=rfq,
                incoming=message,
                manager=manager,
                missing=profile_missing,
                connector=email,
                llm=client,
                profile_instructions=profile_prompt_instructions(audit_start.profile),
                body_override=handoff,
            )
            if handoff is not None:
                audit_start.audit.policy_route = "handoff"
                audit_start.audit.policy_category = "profile_goal_reached"
                audit_start.audit.policy_explanation = (
                    "Цель профиля химика достигнута; данные переданы закупке."
                )
                audit_start.audit.policy_method = "deterministic_profile_rule"
            finalize_usage(
                audit_start.audit,
                client,
                reply_generated=followup_status in {"draft", "sent"},
            )
            db.commit()
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
