"""Временный перевод сохранённой переписки без изменения оригиналов в БД."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.email_text import latest_reply_text
from app.models import Communication
from app.models.enums import Channel, CommDirection
from app.schemas.communication import CommunicationMessageTranslationRead
from app.services.communication_links import communication_linked_to_rfq
from app.services.text_translation import LLMTranslationConnector


def _message_body_for_translation(message: Communication) -> str:
    if (
        message.direction == CommDirection.INBOUND
        and message.channel == Channel.EMAIL
    ):
        return latest_reply_text(message.body)
    return message.body


def translate_communication_messages(
    db: Session,
    *,
    rfq_id: int,
    message_ids: list[int],
    translator: LLMTranslationConnector | None = None,
) -> list[CommunicationMessageTranslationRead]:
    messages = list(
        db.scalars(
            select(Communication)
            .where(
                communication_linked_to_rfq(rfq_id),
                Communication.id.in_(message_ids),
            )
            .order_by(Communication.created_at, Communication.id)
        )
    )
    found_ids = {message.id for message in messages}
    missing_ids = [message_id for message_id in message_ids if message_id not in found_ids]
    if missing_ids:
        raise ValueError("Одно или несколько сообщений не принадлежат этому запросу")

    translation = translator or LLMTranslationConnector()
    return [
        CommunicationMessageTranslationRead(
            message_id=message.id,
            translation_ru=translation.translate(
                _message_body_for_translation(message),
                source_language="auto",
                target_language="ru",
            ),
        )
        for message in messages
        if _message_body_for_translation(message).strip()
    ]
