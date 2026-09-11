"""Подтверждаемая и идемпотентная отправка нескольких RFQ одним сообщением."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.email import EmailConnector, EmailDeliveryError, EmailConfigurationError
from app.connectors.whatsapp import (
    WhatsAppConfigurationError,
    WhatsAppConnector,
    WhatsAppDeliveryError,
)
from app.models import Communication, Manager, PurchaseDecision, RFQ, RfqRecipient, Supplier
from app.models.enums import Channel, CommDirection, DispatchStatus, RFQStatus
from app.services.communication_links import link_communication_to_rfqs
from app.services.integration_settings import effective_email_settings, effective_whatsapp_settings
from app.services.rfq_service import (
    ensure_rfq_english,
    external_rfq_name,
    prepare_rfq_english_text,
    render_rfq_text,
)


@dataclass(frozen=True, slots=True)
class CombinedMessage:
    supplier: Supplier
    manager: Manager
    rfqs: list[RFQ]
    recipients: list[RfqRecipient]
    channel: Channel
    subject: str | None
    body: str


class CombinedDispatchError(RuntimeError):
    """Безопасная ошибка групповой отправки для пользовательского API."""


def _combined_text(rfqs: list[RFQ], channel: Channel) -> tuple[str | None, str]:
    ids = "/".join(f"RFQ-{rfq.id}" for rfq in rfqs)
    subject = f"[{ids}] Request for quotation - multiple products"
    sections: list[str] = [
        "Hello,",
        "",
        "Please quote the following explicitly listed positions. Keep your "
        "answer separated by RFQ number so that conditions are not mixed "
        "between products.",
    ]
    for index, rfq in enumerate(rfqs, start=1):
        _, body = render_rfq_text(rfq)
        sections.extend(
            [
                "",
                f"--- Position {index}: RFQ-{rfq.id} · {external_rfq_name(rfq)} ---",
                body.strip(),
            ]
        )
    sections.extend(
        [
            "",
            "Please reference the RFQ number for every quoted price, unit, "
            "MOQ, grade, Incoterm, payment term, lead time and document.",
        ]
    )
    body = "\n".join(sections)
    ensure_rfq_english(subject, body)
    return (subject if channel == Channel.EMAIL else None), body


def combined_options(
    db: Session,
    *,
    rfqs: list[RFQ],
) -> list[dict]:
    """Показывает только поставщиков, выбранных минимум для двух позиций."""

    rfq_by_id = {rfq.id: rfq for rfq in rfqs}
    if not rfq_by_id:
        return []
    recipients = db.scalars(
        select(RfqRecipient)
        .where(
            RfqRecipient.rfq_id.in_(rfq_by_id),
            RfqRecipient.status == DispatchStatus.QUEUED,
        )
        .order_by(RfqRecipient.supplier_id, RfqRecipient.channel, RfqRecipient.rfq_id)
    ).all()
    grouped: dict[tuple[int, Channel], list[RfqRecipient]] = {}
    for recipient in recipients:
        if recipient.channel != Channel.EMAIL:
            # Групповой входящий разбор пока опирается на Email References.
            # Не предлагаем WhatsApp раньше появления равноценной безопасной
            # маршрутизации provider reply-id ко всем связанным позициям.
            continue
        grouped.setdefault((recipient.supplier_id, recipient.channel), []).append(recipient)
    result: list[dict] = []
    for (supplier_id, channel), items in grouped.items():
        unique_ids = list(dict.fromkeys(item.rfq_id for item in items))
        if len(unique_ids) < 2:
            continue
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            continue
        result.append(
            {
                "supplier_id": supplier.id,
                "supplier_company": supplier.company,
                "channel": channel,
                "positions": [
                    {
                        "rfq_id": rfq_id,
                        "name": rfq_by_id[rfq_id].name,
                        "cas": rfq_by_id[rfq_id].cas,
                        "volume": rfq_by_id[rfq_id].volume,
                    }
                    for rfq_id in unique_ids
                ],
            }
        )
    return result


def prepare_combined_message(
    db: Session,
    *,
    batch_id: int,
    supplier_id: int,
    channel: Channel,
    rfq_ids: list[int],
    accept_sent_recipients: bool = False,
) -> CombinedMessage:
    """Валидирует точный выбор без объединения по похожим названиям."""

    normalized_ids = list(dict.fromkeys(rfq_ids))
    if len(normalized_ids) < 2:
        raise ValueError("Явно выберите не менее двух RFQ")
    if channel != Channel.EMAIL:
        raise ValueError(
            "Общее RFQ для нескольких позиций пока поддерживается только по Email"
        )
    rfqs = list(
        db.scalars(
            select(RFQ)
            .where(
                RFQ.id.in_(normalized_ids),
                RFQ.batch_id == batch_id,
                RFQ.deleted_at.is_(None),
            )
            .order_by(RFQ.id)
        ).all()
    )
    if {rfq.id for rfq in rfqs} != set(normalized_ids):
        raise ValueError("Все выбранные RFQ должны принадлежать этому пакету")
    for rfq in rfqs:
        prepare_rfq_english_text(rfq)
    db.flush()
    decided_ids = set(
        db.scalars(
            select(PurchaseDecision.rfq_id).where(
                PurchaseDecision.rfq_id.in_(normalized_ids)
            )
        ).all()
    )
    if decided_ids:
        raise ValueError(
            "Общая первичная отправка недоступна после сохранения итога закупки"
        )
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ValueError("Поставщик не найден")
    allowed_statuses = [DispatchStatus.QUEUED]
    if accept_sent_recipients:
        allowed_statuses.append(DispatchStatus.SENT)
    recipients = list(
        db.scalars(
            select(RfqRecipient).where(
                RfqRecipient.rfq_id.in_(normalized_ids),
                RfqRecipient.supplier_id == supplier_id,
                RfqRecipient.channel == channel,
                RfqRecipient.status.in_(allowed_statuses),
            )
        ).all()
    )
    if {recipient.rfq_id for recipient in recipients} != set(normalized_ids):
        raise ValueError(
            "Поставщик и канал должны быть явно выбраны для каждой позиции"
        )
    manager = next(
        (
            item
            for item in supplier.managers
            if (channel == Channel.EMAIL and item.email)
            or (channel == Channel.WHATSAPP and item.whatsapp)
        ),
        None,
    )
    if manager is None:
        raise ValueError(
            "У поставщика отсутствует Email"
            if channel == Channel.EMAIL
            else "У поставщика отсутствует WhatsApp"
        )
    subject, body = _combined_text(rfqs, channel)
    return CombinedMessage(supplier, manager, rfqs, recipients, channel, subject, body)


def dispatch_combined_message(
    db: Session,
    *,
    prepared: CombinedMessage,
    idempotency_key: str,
    confirm_external_send: bool,
) -> Communication:
    """Сохраняет одну отправку и связывает её со всеми выбранными позициями."""

    ensure_rfq_english(prepared.subject or "", prepared.body)

    rfq_ids = [rfq.id for rfq in prepared.rfqs]
    existing = db.scalar(
        select(Communication).where(Communication.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_ids = {link.rfq_id for link in existing.rfq_links}
        if (
            existing.manager_id != prepared.manager.id
            or existing.channel != prepared.channel
            or (existing.subject or None) != prepared.subject
            or (existing.body or "") != prepared.body
            or existing_ids != set(rfq_ids)
        ):
            raise ValueError("Ключ повторной отправки уже использован другим сообщением")
        if existing.status in {"sent", "demo"}:
            return existing
        raise CombinedDispatchError(
            "Эта попытка уже зафиксирована и не будет повторена автоматически."
        )

    if prepared.channel == Channel.EMAIL:
        settings, enabled, _ = effective_email_settings(db)
        live = enabled and settings.email_delivery_mode == "live"
        connector: EmailConnector | WhatsAppConnector | None = (
            EmailConnector(settings) if live else None
        )
        recipient = prepared.manager.email or ""
        sender = settings.email_from or None
    else:
        settings, enabled, _ = effective_whatsapp_settings(db)
        live = enabled
        connector = WhatsAppConnector(settings) if live else None
        recipient = prepared.manager.whatsapp or ""
        sender = settings.whatsapp_phone_id or None
    if live and not confirm_external_send:
        raise ValueError("Подтвердите реальную внешнюю отправку общего RFQ")

    communication = Communication(
        rfq_id=None,
        manager_id=prepared.manager.id,
        direction=CommDirection.OUTBOUND,
        channel=prepared.channel,
        subject=prepared.subject,
        body=prepared.body,
        from_address=sender,
        to_address=recipient,
        status="sending" if live else "demo",
        idempotency_key=idempotency_key,
    )
    db.add(communication)
    link_communication_to_rfqs(db, communication=communication, rfq_ids=rfq_ids)
    db.commit()

    if live:
        assert connector is not None
        try:
            if prepared.channel == Channel.EMAIL:
                assert isinstance(connector, EmailConnector)
                provider_id = connector.send(
                    to_address=recipient,
                    subject=prepared.subject or "Request for quotation",
                    body=prepared.body,
                )
            else:
                assert isinstance(connector, WhatsAppConnector)
                provider_id = connector.send_text(to_number=recipient, body=prepared.body)
        except (
            EmailConfigurationError,
            EmailDeliveryError,
            WhatsAppConfigurationError,
            WhatsAppDeliveryError,
        ) as exc:
            communication.status = "delivery_error"
            for item in prepared.recipients:
                item.status = DispatchStatus.ERROR
                item.note = str(exc)[:255]
            db.commit()
            raise CombinedDispatchError(str(exc)) from exc
        communication.external_id = provider_id
        communication.thread_id = provider_id
        communication.status = "sent"

    for item in prepared.recipients:
        item.status = DispatchStatus.SENT
        item.note = (
            "отправлено общим RFQ"
            if live
            else "отправлено общим RFQ (демо)"
        )
    for rfq in prepared.rfqs:
        if rfq.status in {RFQStatus.DRAFT, RFQStatus.VERIFIED}:
            rfq.status = RFQStatus.SENT
    db.commit()
    db.refresh(communication)
    return communication
