"""Automatic processing of inbound WhatsApp Web test-conversation messages."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.connectors.whatsapp import WhatsAppConnector
from app.extraction.llm_client import LLMUnavailableError
from app.models import CommunicationTestMessage, CommunicationTestRun
from app.services.communication_policy import classify_supplier_message
from app.services.communication_profiles import (
    budget_escalation_note,
    finalize_usage,
    record_policy,
    start_audit,
)
from app.services.communication_recipient import recipient_key, reveal_recipient
from app.services.communication_testing import (
    CommunicationTestError,
    _communication_test_llm_client,
    _continue_prompt,
    _generate_reply,
)
from app.services.integration_settings import effective_whatsapp_settings


def accept_incoming_whatsapp(
    db: Session,
    *,
    message_id: str,
    from_number: str,
    body: str,
) -> tuple[str, int | None]:
    """Idempotently stores an inbound event and returns its processing state."""
    existing = db.scalar(
        select(CommunicationTestMessage).where(
            CommunicationTestMessage.provider_message_id == message_id
        )
    )
    if existing is not None:
        return "duplicate", existing.run_id

    run = db.scalar(
        select(CommunicationTestRun)
        .options(selectinload(CommunicationTestRun.messages))
        .where(
            CommunicationTestRun.channel == "whatsapp",
            CommunicationTestRun.delivery_mode == "send",
            CommunicationTestRun.recipient_key
            == recipient_key("whatsapp", from_number),
        )
        .order_by(CommunicationTestRun.id.desc())
        .limit(1)
    )
    if run is None:
        return "unmatched", None

    run.messages.append(
        CommunicationTestMessage(
            run_id=run.id,
            sender_role="supplier",
            content=body.strip(),
            delivery_status="received",
            provider_message_id=message_id,
        )
    )
    run.customer_message = body.strip()
    run.status = "classifying"
    run.error = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return "duplicate", run.id
    return "accepted", run.id


def _escalate(
    db: Session,
    run: CommunicationTestRun,
    *,
    explanation: str,
    category: str,
) -> None:
    run.status = "escalated"
    run.error = (
        "Требуется ответ человека: "
        f"{explanation} Категория: {category}."
    )
    db.commit()


def process_incoming_whatsapp(db: Session, *, run_id: int, message_id: str) -> None:
    """Classifies one already-persisted reply and sends at most one AI response."""
    run = db.scalar(
        select(CommunicationTestRun)
        .options(selectinload(CommunicationTestRun.messages))
        .where(CommunicationTestRun.id == run_id)
    )
    if run is None or run.status != "classifying":
        return
    incoming = next(
        (
            item
            for item in run.messages
            if item.provider_message_id == message_id
            and item.sender_role == "supplier"
        ),
        None,
    )
    if incoming is None:
        return

    try:
        audit_start = start_audit(
            db,
            event_key=f"communication-test-whatsapp:{message_id}",
            text=incoming.content,
            rfq_id=run.rfq_id,
            test_run_id=run.id,
            actor_id=run.actor_id,
        )
        if not audit_start.budget.allowed:
            _escalate(
                db,
                run,
                explanation=budget_escalation_note(audit_start.audit),
                category="budget_limit",
            )
            return
        try:
            llm = _communication_test_llm_client()
        except LLMUnavailableError:
            audit_start.audit.policy_route = "escalate"
            audit_start.audit.policy_category = "unclear"
            audit_start.audit.policy_explanation = "Нейросеть недоступна."
            audit_start.audit.policy_method = "safe_fallback"
            _escalate(
                db,
                run,
                explanation="Нейросеть недоступна, безопасная классификация не выполнена.",
                category="unclear",
            )
            return

        policy = classify_supplier_message(
            incoming.content,
            rfq_name=run.procurement_context,
            rfq_cas=None,
            llm=llm,
        )
        record_policy(audit_start.audit, policy)
        if not policy.auto_reply_allowed:
            finalize_usage(audit_start.audit, llm, reply_generated=False)
            _escalate(
                db,
                run,
                explanation=policy.explanation,
                category=policy.category,
            )
            return

        run.status = "generating"
        db.commit()
        reply = _generate_reply(
            db,
            run=run,
            user_text=_continue_prompt(run),
            stage="reply",
            llm=llm,
        )
        finalize_usage(audit_start.audit, llm, reply_generated=True)
        outgoing = CommunicationTestMessage(
            run_id=run.id,
            sender_role="assistant",
            content=reply,
            translation_ru=None,
            delivery_status="sending",
        )
        run.messages.append(outgoing)
        run.generated_reply = reply
        run.status = "sending"
        db.commit()

        if not run.recipient_ciphertext:
            raise CommunicationTestError("Получатель WhatsApp не сохранён")
        settings, enabled, _ = effective_whatsapp_settings(db)
        if not enabled or settings.whatsapp_transport != "web":
            raise CommunicationTestError("WhatsApp Web не включён")
        provider_id = WhatsAppConnector(settings).send_text(
            to_number=reveal_recipient(run.recipient_ciphertext), body=reply
        )
        outgoing.provider_message_id = provider_id
        outgoing.delivery_status = "sent"
        run.provider_message_id = provider_id
        run.status = "sent"
        run.error = None
        db.commit()
    except Exception:
        db.rollback()
        current = db.get(CommunicationTestRun, run_id)
        if current is not None:
            current.status = "processing_error"
            current.error = (
                "Автоматическая обработка WhatsApp остановлена. "
                "Требуется ответ человека; повторная автоотправка заблокирована."
            )
            db.commit()
