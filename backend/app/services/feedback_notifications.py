"""Внутренние Email-уведомления о новой обратной связи."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parseaddr

from sqlalchemy.orm import Session

from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
)
from app.models import User
from app.models.feedback import FeedbackMessage
from app.services.integration_settings import (
    IntegrationSettingsError,
    effective_email_settings,
)

logger = logging.getLogger(__name__)


def _save_status(
    db: Session,
    message: FeedbackMessage,
    status: str,
    *,
    message_id: str | None = None,
) -> None:
    message.email_delivery_status = status
    if message_id is not None:
        message.email_message_id = message_id
    db.add(message)
    db.commit()
    db.refresh(message)


def deliver_feedback_notification(
    db: Session,
    *,
    message: FeedbackMessage,
    author: User,
) -> None:
    """Отправляет уведомление, не превращая сбой SMTP в потерю обращения."""
    try:
        settings, enabled, _ = effective_email_settings(db)
    except IntegrationSettingsError as exc:
        _save_status(db, message, "failed")
        logger.warning(
            "Feedback email settings could not be read for message %s (%s)",
            message.id,
            type(exc).__name__,
        )
        return

    recipient = getattr(settings, "feedback_email_to", "").strip()
    if not recipient or not enabled or settings.email_delivery_mode != "live":
        _save_status(db, message, "disabled")
        return

    sender = parseaddr(settings.email_from)[1].strip()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else "chemsource.local"
    technical_id = f"<feedback-{message.id}@{domain}>"
    message.email_delivery_attempted_at = datetime.now(timezone.utc)
    _save_status(db, message, "sending", message_id=technical_id)

    origin = message.origin or "вся программа"
    body = (
        "В ChemSource AI поступило новое сообщение обратной связи.\n\n"
        f"Номер: {message.id}\n"
        f"Автор: {author.full_name} ({author.username})\n"
        f"Роль: {author.role.value}\n"
        f"Раздел: {origin}\n\n"
        "Сообщение:\n"
        f"{message.text}\n"
    )
    try:
        delivered_id = EmailConnector(settings).send(
            to_address=recipient,
            subject=f"ChemSource AI — новая обратная связь #{message.id}",
            body=body,
            message_id=technical_id,
        )
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        _save_status(db, message, "failed")
        logger.warning(
            "Feedback email delivery failed for message %s (%s)",
            message.id,
            type(exc).__name__,
        )
        return

    _save_status(db, message, "sent", message_id=delivered_id)
