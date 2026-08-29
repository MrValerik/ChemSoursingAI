"""Общий служебный почтовый ящик поверх сохранённых Email-коммуникаций."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
)
from app.models.communication import Communication
from app.models.enums import Channel, CommDirection
from app.services.communication_delivery import CommunicationSendError
from app.services.integration_settings import effective_email_settings


def send_mailbox_message(
    db: Session,
    *,
    to_address: str,
    subject: str,
    body: str,
    idempotency_key: str,
    reply_to_message_id: int | None = None,
) -> Communication:
    """Отправляет одно письмо из служебного ящика с защитой от дубля."""
    recipient = parseaddr(to_address)[1].strip().casefold()
    clean_subject = " ".join(subject.replace("\r", " ").splitlines()).strip()
    clean_body = body.strip()
    if not recipient or "@" not in recipient:
        raise ValueError("Укажите корректный Email получателя")
    if not clean_subject:
        raise ValueError("Укажите тему письма")
    if not clean_body:
        raise ValueError("Введите текст письма")

    existing = db.scalar(
        select(Communication).where(
            Communication.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.channel != Channel.EMAIL
            or existing.to_address != recipient
            or (existing.subject or "") != clean_subject
            or (existing.body or "") != clean_body
        ):
            raise ValueError("Ключ повторной отправки уже использован другим письмом")
        if existing.status == "sent":
            return existing
        raise CommunicationSendError(
            "Эта попытка уже зафиксирована и не будет повторена автоматически, "
            "чтобы не отправить письмо дважды. Создайте новое письмо."
        )

    reply_target = None
    if reply_to_message_id is not None:
        reply_target = db.get(Communication, reply_to_message_id)
        if reply_target is None or reply_target.channel != Channel.EMAIL:
            raise ValueError("Исходное письмо для ответа не найдено")

    settings, enabled, _ = effective_email_settings(db)
    if not enabled or settings.email_delivery_mode != "live":
        raise ValueError(
            "Реальная Email-отправка выключена. Включите канал и режим Live "
            "в настройках."
        )

    reply_reference = None
    if reply_target is not None:
        reply_reference = reply_target.external_id or reply_target.thread_id
        expected_recipient = (
            reply_target.from_address
            if reply_target.direction == CommDirection.INBOUND
            else reply_target.to_address
        )
        expected_recipient = parseaddr(expected_recipient or "")[1].strip().casefold()
        if expected_recipient and recipient != expected_recipient:
            raise ValueError(
                "Адрес получателя ответа не совпадает с выбранной перепиской"
            )

    communication = Communication(
        rfq_id=reply_target.rfq_id if reply_target is not None else None,
        manager_id=reply_target.manager_id if reply_target is not None else None,
        direction=CommDirection.OUTBOUND,
        channel=Channel.EMAIL,
        subject=clean_subject,
        body=clean_body,
        from_address=settings.email_from or None,
        to_address=recipient,
        status="sending",
        thread_id=reply_reference,
        external_id=None,
        idempotency_key=idempotency_key,
        attachments=None,
    )
    db.add(communication)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        concurrent = db.scalar(
            select(Communication).where(
                Communication.idempotency_key == idempotency_key
            )
        )
        if concurrent is not None and concurrent.status == "sent":
            return concurrent
        raise CommunicationSendError(
            "Эта отправка уже выполняется и не будет запущена повторно."
        ) from exc
    db.refresh(communication)

    domain = (settings.email_from.rpartition("@")[2] or "chemsource.local").strip()
    provider_message_id = f"<mail-{idempotency_key}@{domain}>"
    try:
        provider_id = EmailConnector(settings).send(
            to_address=recipient,
            subject=clean_subject,
            body=clean_body,
            in_reply_to=reply_reference,
            references=[reply_reference] if reply_reference else None,
            message_id=provider_message_id,
        )
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(str(exc)) from exc
    except Exception as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(
            "Почтовый сервер не подтвердил отправку. Повтор не выполнен, "
            "чтобы избежать дубля."
        ) from exc

    communication.external_id = provider_id
    communication.status = "sent"
    communication.message_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(communication)
    return communication
