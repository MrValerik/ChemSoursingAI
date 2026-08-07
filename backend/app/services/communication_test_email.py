"""Автоматическое продолжение реальных Email-диалогов тестовой песочницы."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.connectors.email import EmailConnector, IncomingEmail
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import CommunicationTestMessage, CommunicationTestRun
from app.services.communication_policy import classify_supplier_message
from app.services.communication_testing import (
    CommunicationTestError,
    _communication_test_llm_client,
    _continue_prompt,
    _generate_reply,
)
from app.services.integration_settings import effective_email_settings


@dataclass(slots=True)
class CommunicationTestEmailSyncSummary:
    fetched: int = 0
    matched: int = 0
    processed: int = 0
    replied: int = 0
    escalated: int = 0
    duplicates: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)


def _thread_ids(message: IncomingEmail) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for value in [message.in_reply_to, *message.references]
            if value
        )
    )


def _find_run(db: Session, message: IncomingEmail) -> CommunicationTestRun | None:
    references = _thread_ids(message)
    if not references:
        return None
    return db.scalar(
        select(CommunicationTestRun)
        .join(CommunicationTestMessage)
        .options(selectinload(CommunicationTestRun.messages))
        .where(
            CommunicationTestRun.channel == "email",
            CommunicationTestRun.delivery_mode == "send",
            CommunicationTestMessage.sender_role == "assistant",
            CommunicationTestMessage.provider_message_id.in_(references),
        )
        .order_by(CommunicationTestRun.id.desc())
        .limit(1)
    )


def _existing_message(
    db: Session, message_id: str
) -> CommunicationTestMessage | None:
    return db.scalar(
        select(CommunicationTestMessage)
        .options(
            selectinload(CommunicationTestMessage.run).selectinload(
                CommunicationTestRun.messages
            )
        )
        .where(CommunicationTestMessage.provider_message_id == message_id)
    )


def _can_resume_before_smtp(message: CommunicationTestMessage) -> bool:
    """Разрешает восстановить только этап до зафиксированной SMTP-попытки."""
    run = message.run
    return (
        message.sender_role == "supplier"
        and run.status in {"classifying", "generating"}
        and not any(
            item.sender_role == "assistant" and item.id > message.id
            for item in run.messages
        )
    )


def _reply_subject(message: IncomingEmail, run: CommunicationTestRun) -> str:
    subject = message.subject.strip() or run.subject
    return subject if subject.casefold().startswith("re:") else f"Re: {subject}"


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


def _terminal_error(db: Session, run_id: int, message: str) -> None:
    run = db.get(CommunicationTestRun, run_id)
    if run is None:
        return
    run.status = "processing_error"
    run.error = message
    db.commit()


def sync_communication_test_email(
    db: Session,
    connector: EmailConnector | None = None,
    *,
    llm: LLMClient | None = None,
    limit: int = 20,
) -> CommunicationTestEmailSyncSummary:
    """Обрабатывает только ответы на отправленные тестовые Email один раз.

    Входящее письмо сначала фиксируется в БД с уникальным Message-ID. Поэтому
    повторный IMAP-опрос или неопределённый результат SMTP не может привести к
    повторной внешней отправке.
    """
    summary = CommunicationTestEmailSyncSummary()
    email = connector
    if email is None:
        settings, enabled, _ = effective_email_settings(db)
        if not enabled or settings.email_delivery_mode != "live":
            return summary
        email = EmailConnector(settings)

    messages = email.fetch_unseen(limit=limit)
    summary.fetched = len(messages)
    seen_uids: list[str] = []

    for incoming in messages:
        run_id: int | None = None
        inbound_saved = False
        try:
            existing = _existing_message(db, incoming.message_id)
            if existing is not None:
                if _can_resume_before_smtp(existing):
                    # Процесс мог завершиться после записи входящего письма, но
                    # до SMTP. Повторить классификацию/генерацию безопасно.
                    run = existing.run
                    run_id = run.id
                    inbound_saved = True
                    summary.matched += 1
                else:
                    summary.duplicates += 1
                    seen_uids.append(incoming.uid)
                    continue
            else:
                run = _find_run(db, incoming)
                if run is None:
                    summary.unmatched += 1
                    continue
                summary.matched += 1
                run_id = run.id

                inbound = CommunicationTestMessage(
                    run_id=run.id,
                    sender_role="supplier",
                    content=incoming.text,
                    delivery_status="received",
                    provider_message_id=incoming.message_id,
                )
                run.messages.append(inbound)
                run.customer_message = incoming.text
                run.status = "classifying"
                run.error = None
                try:
                    db.commit()
                    inbound_saved = True
                except IntegrityError:
                    db.rollback()
                    summary.duplicates += 1
                    seen_uids.append(incoming.uid)
                    continue

            try:
                client = llm or _communication_test_llm_client()
            except LLMUnavailableError:
                _escalate(
                    db,
                    run,
                    explanation=(
                        "Нейросеть недоступна, поэтому безопасная "
                        "классификация не выполнена."
                    ),
                    category="unclear",
                )
                summary.escalated += 1
                summary.processed += 1
                seen_uids.append(incoming.uid)
                continue

            policy = classify_supplier_message(
                incoming.text,
                rfq_name=run.procurement_context,
                rfq_cas=None,
                llm=client,
            )
            if not policy.auto_reply_allowed:
                _escalate(
                    db,
                    run,
                    explanation=policy.explanation,
                    category=policy.category,
                )
                summary.escalated += 1
                summary.processed += 1
                seen_uids.append(incoming.uid)
                continue

            run.status = "generating"
            db.commit()
            reply = _generate_reply(
                db,
                run=run,
                user_text=_continue_prompt(run),
                stage="reply",
                llm=client,
            )

            outgoing = CommunicationTestMessage(
                run_id=run.id,
                sender_role="assistant",
                content=reply,
                # Важно сохранить попытку до SMTP: состояние sending означает,
                # что результат внешней операции мог быть неопределённым.
                delivery_status="sending",
            )
            run.messages.append(outgoing)
            run.generated_reply = reply
            run.status = "sending"
            run.error = None
            db.commit()

            try:
                provider_id = email.send(
                    to_address=incoming.from_address,
                    subject=_reply_subject(incoming, run),
                    body=reply,
                    in_reply_to=incoming.message_id,
                    references=[*incoming.references, incoming.message_id],
                )
            except Exception as exc:
                outgoing.delivery_status = "delivery_error"
                run.status = "delivery_error"
                run.error = (
                    "Email-ответ не подтверждён провайдером. Повторная "
                    "автоотправка заблокирована, чтобы не создать дубликат."
                )
                db.commit()
                summary.errors.append(
                    f"{incoming.message_id}: {type(exc).__name__}"
                )
                summary.processed += 1
                seen_uids.append(incoming.uid)
                continue

            outgoing.provider_message_id = provider_id
            outgoing.delivery_status = "sent"
            run.provider_message_id = provider_id
            run.status = "sent"
            db.commit()
            summary.replied += 1
            summary.processed += 1
            seen_uids.append(incoming.uid)
        except CommunicationTestError as exc:
            db.rollback()
            summary.errors.append(
                f"{incoming.message_id}: CommunicationTestError: {exc}"
            )
            summary.processed += 1
            if inbound_saved:
                seen_uids.append(incoming.uid)
        except Exception as exc:
            db.rollback()
            summary.errors.append(
                f"{incoming.message_id}: {type(exc).__name__}"
            )
            if inbound_saved and run_id is not None:
                _terminal_error(
                    db,
                    run_id,
                    "Автоматическая обработка остановлена из-за внутренней "
                    "ошибки. Требуется ответ человека.",
                )
                summary.processed += 1
                seen_uids.append(incoming.uid)

    if seen_uids:
        try:
            email.mark_seen(seen_uids)
        except Exception as exc:
            summary.errors.append(f"IMAP mark_seen: {type(exc).__name__}")
    return summary
