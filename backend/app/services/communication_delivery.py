"""Явная и идемпотентная отправка сообщений из рабочего диалога."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
)
from app.connectors.whatsapp import (
    WhatsAppConfigurationError,
    WhatsAppConnector,
    WhatsAppDeliveryError,
)
from app.models import Communication, Manager, RFQ
from app.models.enums import Channel, CommDirection
from app.services.integration_settings import (
    effective_email_settings,
    effective_whatsapp_settings,
)
from app.services.document_storage import (
    DocumentTooLargeError,
    UnsupportedDocumentError,
    safe_filename,
    store_document,
)


class CommunicationSendError(RuntimeError):
    """Безопасная ошибка внешней отправки для показа пользователю."""


def _raw_attachment_signature(attachments: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (
            safe_filename(str(item.get("filename") or "document")),
            hashlib.sha256(bytes(item.get("content") or b"")).hexdigest(),
        )
        for item in attachments
    ]


def _stored_attachment_signature(
    attachments: list[dict[str, Any]] | None,
) -> list[tuple[str, str]]:
    return [
        (
            safe_filename(str(item.get("filename") or "document")),
            str(item.get("sha256") or ""),
        )
        for item in attachments or []
    ]


def _store_outbound_attachments(
    db: Session,
    *,
    rfq_id: int,
    communication_id: int,
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stored_metadata: list[dict[str, Any]] = []
    for attachment in attachments:
        payload = attachment.get("content")
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("Содержимое исходящего файла недоступно")
        try:
            stored = store_document(
                db,
                payload=bytes(payload),
                filename=str(attachment.get("filename") or "document"),
                declared_content_type=str(
                    attachment.get("content_type") or "application/octet-stream"
                ),
                rfq_id=rfq_id,
                communication_id=communication_id,
            )
        except (DocumentTooLargeError, UnsupportedDocumentError) as exc:
            raise ValueError(str(exc)) from exc
        document = stored.document
        # В исходящем файле текст не извлекаем: он сохраняется для аудита и
        # скачивания, но не является ответом поставщика или доказательством.
        stored_metadata.append(
            {
                "filename": document.filename,
                "content_type": document.content_type,
                "size": document.size_bytes,
                "document_id": document.id,
                "sha256": document.sha256,
                "kind": document.kind,
                "status": "sent_file",
            }
        )
        attachment["filename"] = document.filename
        attachment["content_type"] = document.content_type
    return stored_metadata


def _latest_message(
    db: Session,
    *,
    rfq_id: int,
    manager_id: int,
    channel: Channel,
) -> Communication | None:
    return db.scalar(
        select(Communication)
        .where(
            Communication.rfq_id == rfq_id,
            Communication.manager_id == manager_id,
            Communication.channel == channel,
        )
        .order_by(Communication.created_at.desc(), Communication.id.desc())
        .limit(1)
    )


def _email_subject(rfq: RFQ, latest: Communication | None, requested: str | None) -> str:
    subject = (requested or "").strip()
    if subject:
        return subject
    previous = (latest.subject if latest else None) or f"[RFQ-{rfq.id}] {rfq.name}"
    return previous if previous.casefold().startswith("re:") else f"Re: {previous}"


def _email_references(
    db: Session,
    *,
    rfq_id: int,
    manager_id: int,
) -> list[str]:
    values = list(
        db.scalars(
            select(Communication.external_id)
            .where(
                Communication.rfq_id == rfq_id,
                Communication.manager_id == manager_id,
                Communication.channel == Channel.EMAIL,
                Communication.external_id.is_not(None),
            )
            .order_by(Communication.created_at.desc(), Communication.id.desc())
            .limit(20)
        ).all()
    )
    return list(reversed([value for value in values if value]))


def send_conversation_message(
    db: Session,
    *,
    rfq: RFQ,
    manager_id: int,
    channel: Channel,
    body: str,
    subject: str | None,
    idempotency_key: str,
    attachments: list[dict[str, Any]] | None = None,
) -> Communication:
    """Отправляет сообщение один раз и сохраняет попытку до сетевого вызова."""
    clean_body = body.strip()
    outbound_attachments = list(attachments or [])
    attachment_signature = _raw_attachment_signature(outbound_attachments)
    existing = db.scalar(
        select(Communication).where(
            Communication.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.rfq_id != rfq.id
            or existing.manager_id != manager_id
            or existing.channel != channel
            or (existing.body or "") != clean_body
            or _stored_attachment_signature(existing.attachments)
            != attachment_signature
        ):
            raise ValueError("Ключ повторной отправки уже использован другим сообщением")
        if existing.status == "sent":
            return existing
        raise CommunicationSendError(
            "Эта попытка уже зафиксирована и не будет повторена автоматически, "
            "чтобы не отправить сообщение дважды. Обновите диалог."
        )

    manager = db.get(Manager, manager_id)
    if manager is None:
        raise ValueError("Контакт поставщика не найден")
    latest = _latest_message(
        db,
        rfq_id=rfq.id,
        manager_id=manager.id,
        channel=channel,
    )
    if latest is None:
        raise ValueError("Диалог с этим контактом ещё не начат")

    if not clean_body and not outbound_attachments:
        raise ValueError("Введите текст сообщения или прикрепите файл")

    if channel == Channel.EMAIL:
        recipient = (manager.email or "").strip()
        if not recipient:
            raise ValueError("У контакта поставщика отсутствует Email")
        settings, enabled, _ = effective_email_settings(db)
        if not enabled or settings.email_delivery_mode != "live":
            raise ValueError(
                "Реальная Email-отправка выключена. Включите канал и режим Live "
                "в настройках."
            )
        connector: EmailConnector | WhatsAppConnector = EmailConnector(settings)
        outgoing_subject = _email_subject(rfq, latest, subject)
        reply_to = latest.external_id or latest.thread_id
        from_address = settings.email_from or None
    else:
        recipient = (manager.whatsapp or "").strip()
        if not recipient:
            raise ValueError("У контакта поставщика отсутствует WhatsApp")
        settings, enabled, _ = effective_whatsapp_settings(db)
        if not enabled:
            raise ValueError(
                "Реальная отправка WhatsApp выключена. Включите канал в настройках."
            )
        connector = WhatsAppConnector(settings)
        outgoing_subject = None
        reply_to = latest.external_id or latest.thread_id
        from_address = settings.whatsapp_phone_id or None

    communication = Communication(
        rfq_id=rfq.id,
        manager_id=manager.id,
        direction=CommDirection.OUTBOUND,
        channel=channel,
        subject=outgoing_subject,
        body=clean_body,
        from_address=from_address,
        to_address=recipient,
        status="sending",
        thread_id=reply_to,
        external_id=None,
        idempotency_key=idempotency_key,
        attachments=None,
    )
    db.add(communication)
    try:
        db.flush()
        communication.attachments = (
            _store_outbound_attachments(
                db,
                rfq_id=rfq.id,
                communication_id=communication.id,
                attachments=outbound_attachments,
            )
            or None
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        concurrent = db.scalar(
            select(Communication).where(
                Communication.idempotency_key == idempotency_key
            )
        )
        if (
            concurrent is not None
            and concurrent.rfq_id == rfq.id
            and concurrent.manager_id == manager_id
            and concurrent.channel == channel
            and (concurrent.body or "") == clean_body
            and _stored_attachment_signature(concurrent.attachments)
            == attachment_signature
            and concurrent.status == "sent"
        ):
            return concurrent
        raise CommunicationSendError(
            "Эта отправка уже выполняется и не будет запущена повторно."
        ) from exc
    db.refresh(communication)

    try:
        if channel == Channel.EMAIL:
            assert isinstance(connector, EmailConnector)
            provider_id = connector.send(
                to_address=recipient,
                subject=outgoing_subject or f"[RFQ-{rfq.id}] {rfq.name}",
                body=clean_body,
                in_reply_to=reply_to,
                references=_email_references(
                    db,
                    rfq_id=rfq.id,
                    manager_id=manager.id,
                ),
                attachments=outbound_attachments,
            )
        else:
            assert isinstance(connector, WhatsAppConnector)
            if outbound_attachments:
                provider_ids: list[str] = []
                for index, attachment in enumerate(outbound_attachments):
                    current_id = connector.send_document(
                        to_number=recipient,
                        filename=str(attachment["filename"]),
                        content_type=str(attachment["content_type"]),
                        content=bytes(attachment["content"]),
                        caption=clean_body if index == 0 else "",
                    )
                    provider_ids.append(current_id)
                    if communication.attachments:
                        updated_metadata = [
                            dict(item) for item in communication.attachments
                        ]
                        updated_metadata[index]["provider_message_id"] = current_id
                        communication.attachments = updated_metadata
                provider_id = provider_ids[0]
            else:
                provider_id = connector.send_text(
                    to_number=recipient,
                    body=clean_body,
                )
    except (
        EmailConfigurationError,
        EmailDeliveryError,
        WhatsAppConfigurationError,
        WhatsAppDeliveryError,
    ) as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(str(exc)) from exc
    except Exception as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(
            "Провайдер не подтвердил отправку. Повтор не выполнен, чтобы избежать дубля."
        ) from exc

    communication.external_id = provider_id
    communication.status = "sent"
    if channel == Channel.WHATSAPP:
        communication.thread_id = provider_id
    db.commit()
    db.refresh(communication)
    return communication


def send_email_draft(
    db: Session,
    *,
    communication: Communication,
) -> Communication:
    """Отправляет сохранённый Email-черновик не более одного раза."""
    if (
        communication.direction != CommDirection.OUTBOUND
        or communication.channel != Channel.EMAIL
    ):
        raise ValueError("Отправить можно только исходящий Email-черновик")
    if communication.status == "sent":
        return communication
    if communication.status != "draft":
        raise ValueError("Отправить можно только исходящий Email-черновик")
    if not communication.to_address:
        raise ValueError("У черновика отсутствует адрес получателя")

    settings, enabled, _ = effective_email_settings(db)
    if not enabled or settings.email_delivery_mode != "live":
        raise ValueError(
            "Реальная Email-отправка выключена. Включите канал и режим Live "
            "в настройках."
        )

    communication.status = "sending"
    communication.from_address = settings.email_from or None
    db.commit()
    try:
        provider_id = EmailConnector(settings).send(
            to_address=communication.to_address,
            subject=communication.subject or "RFQ follow-up",
            body=communication.body or "",
            in_reply_to=communication.thread_id,
            references=[communication.thread_id] if communication.thread_id else None,
        )
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(str(exc)) from exc
    except Exception as exc:
        communication.status = "delivery_error"
        db.commit()
        raise CommunicationSendError(
            "Провайдер не подтвердил отправку. Повтор не выполнен, чтобы избежать дубля."
        ) from exc

    communication.external_id = provider_id
    communication.status = "sent"
    db.commit()
    db.refresh(communication)
    return communication
