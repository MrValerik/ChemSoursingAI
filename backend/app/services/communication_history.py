"""Собирает единый обзор переписки RFQ по поставщикам и каналам."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.extraction.email_text import latest_reply_text
from app.models import Communication, Escalation, Quotation, RFQ, RfqRecipient, Supplier
from app.models.enums import Channel, CommDirection, DispatchStatus, EscalationStatus
from app.models.manager import Manager
from app.schemas.communication import (
    CommunicationEscalationRead,
    CommunicationMessageRead,
    CommunicationOverviewRead,
    SupplierConversationRead,
)
from app.services.completeness import accumulate_quotations
from app.services.communication_links import communication_linked_to_rfq


def _contact(manager: Manager | None, channel: Channel) -> str | None:
    if manager is None:
        return None
    return manager.email if channel == Channel.EMAIL else manager.whatsapp


def _message_contact(message: Communication) -> str | None:
    if message.direction == CommDirection.INBOUND:
        return message.from_address
    return message.to_address


def _message_body_for_display(message: Communication) -> str:
    """Скрывает цитату старой Email-цепочки, не меняя оригинал в БД."""
    if (
        message.direction == CommDirection.INBOUND
        and message.channel == Channel.EMAIL
    ):
        return latest_reply_text(message.body)
    return message.body


def _contact_key(channel: Channel, value: str | None) -> tuple[str, str, str]:
    return ("contact", channel.value, (value or "неизвестный контакт").casefold())


def _supplier_key(supplier_id: int, channel: Channel) -> tuple[str, str, str]:
    return ("supplier", channel.value, str(supplier_id))


def _new_conversation(
    *,
    supplier: Supplier | None,
    manager: Manager | None,
    channel: Channel,
    contact: str | None,
    recipient_status: DispatchStatus | None,
) -> SupplierConversationRead:
    return SupplierConversationRead(
        supplier_id=supplier.id if supplier else None,
        supplier_company=(
            supplier.company if supplier else contact or "Неизвестный контакт"
        ),
        manager_id=manager.id if manager else None,
        manager_name=manager.full_name if manager else None,
        channel=channel,
        contact=contact,
        # Адреса ниже собираются из фактических сообщений по хронологии:
        # первый — исходный получатель, последующие — связанные ответы.
        linked_contacts=[],
        recipient_status=recipient_status,
        last_message_at=None,
    )


def list_communication_overview(
    db: Session, rfq_id: int
) -> CommunicationOverviewRead:
    recipients = list(
        db.scalars(
            select(RfqRecipient)
            .options(
                joinedload(RfqRecipient.supplier).joinedload(Supplier.managers)
            )
            .where(RfqRecipient.rfq_id == rfq_id)
            .order_by(RfqRecipient.id)
        ).unique()
    )
    messages = list(
        db.scalars(
            select(Communication)
            .options(
                joinedload(Communication.manager).joinedload(Manager.supplier),
                joinedload(Communication.rfq_links),
            )
            .where(communication_linked_to_rfq(rfq_id))
            .order_by(Communication.created_at, Communication.id)
        ).unique()
    )
    escalations = list(
        db.scalars(
            select(Escalation)
            .options(
                joinedload(Escalation.manager).joinedload(Manager.supplier),
                joinedload(Escalation.communication)
                .joinedload(Communication.manager)
                .joinedload(Manager.supplier),
            )
            .where(Escalation.rfq_id == rfq_id)
            .order_by(Escalation.created_at.desc(), Escalation.id.desc())
        ).unique()
    )
    quotations = list(
        db.scalars(
            select(Quotation)
            .options(
                joinedload(Quotation.manager).joinedload(Manager.supplier)
            )
            .where(Quotation.rfq_id == rfq_id)
            .order_by(Quotation.created_at, Quotation.id)
        ).unique()
    )
    linked_rfq_ids = {
        linked_id
        for message in messages
        for linked_id in {
            *[link.rfq_id for link in message.rfq_links],
            *([message.rfq_id] if message.rfq_id is not None else []),
        }
    }
    linked_rfq_by_id = {
        rfq.id: rfq
        for rfq in db.scalars(
            select(RFQ).where(RFQ.id.in_(linked_rfq_ids))
        ).all()
    }

    conversations: dict[tuple[str, str, str], SupplierConversationRead] = {}
    contacts: dict[tuple[Channel, str], tuple[Supplier, Manager]] = {}
    # Диалоги, в которых компания хоть раз написала сама.
    answered: set[tuple[str, str, str]] = set()

    for recipient in recipients:
        supplier = recipient.supplier
        if supplier is None:
            continue
        managers = [
            manager
            for manager in supplier.managers
            if _contact(manager, recipient.channel)
        ]
        manager = managers[0] if managers else None
        contact = _contact(manager, recipient.channel)
        if manager and contact:
            contacts[(recipient.channel, contact.casefold())] = (supplier, manager)
        # Получатель в очереди ещё не является начатым диалогом.
        if recipient.status == DispatchStatus.QUEUED:
            continue
        key = _supplier_key(supplier.id, recipient.channel)
        conversations[key] = _new_conversation(
            supplier=supplier,
            manager=manager,
            channel=recipient.channel,
            contact=contact,
            recipient_status=recipient.status,
        )

    for message in messages:
        manager = message.manager
        supplier = manager.supplier if manager else None
        contact = _message_contact(message) or _contact(manager, message.channel)
        if supplier is None and contact:
            resolved = contacts.get((message.channel, contact.casefold()))
            if resolved:
                supplier, manager = resolved
        key = (
            _supplier_key(supplier.id, message.channel)
            if supplier
            else _contact_key(message.channel, contact)
        )
        conversation = conversations.get(key)
        if conversation is None:
            conversation = _new_conversation(
                supplier=supplier,
                manager=manager,
                channel=message.channel,
                contact=contact,
                recipient_status=None,
            )
            conversations[key] = conversation
        elif manager is not None and (
            conversation.manager_id is None
            or message.direction == CommDirection.INBOUND
        ):
            # RFQ мог уйти на общий sales@, а ответить личный менеджер с
            # другого адреса компании. Диалог остаётся один на поставщика,
            # но следующий ответ должен идти на последний входящий адрес.
            conversation.manager_id = manager.id
            conversation.manager_name = manager.full_name
            conversation.contact = contact
        if contact and all(
            saved.casefold() != contact.casefold()
            for saved in conversation.linked_contacts
        ):
            conversation.linked_contacts.append(contact)
        conversation.messages.append(
            CommunicationMessageRead(
                id=message.id,
                direction=message.direction,
                channel=message.channel,
                subject=message.subject,
                body=_message_body_for_display(message),
                status=message.status,
                from_address=message.from_address,
                to_address=message.to_address,
                attachments=message.attachments,
                linked_rfqs=[
                    {
                        "rfq_id": linked_id,
                        "name": linked_rfq_by_id[linked_id].name,
                        "cas": linked_rfq_by_id[linked_id].cas,
                    }
                    for linked_id in sorted(
                        {
                            *[link.rfq_id for link in message.rfq_links],
                            *(
                                [message.rfq_id]
                                if message.rfq_id is not None
                                else []
                            ),
                        }
                    )
                    if linked_id in linked_rfq_by_id
                ],
                created_at=message.created_at,
            )
        )
        # Дата письма, а не момент вставки строки: синхронизация ящика
        # заводит месячную переписку одним заходом, и по created_at все
        # диалоги выглядели бы свежими.
        conversation.last_message_at = message.message_at or message.created_at
        if message.direction == CommDirection.INBOUND:
            answered.add(key)

    unassigned: list[CommunicationEscalationRead] = []
    for escalation in escalations:
        communication = escalation.communication
        manager = escalation.manager or (
            communication.manager if communication else None
        )
        supplier = manager.supplier if manager else None
        escalation_read = CommunicationEscalationRead(
            id=escalation.id,
            reason=escalation.reason.value,
            status=escalation.status.value,
            assignee=escalation.assignee,
            note=escalation.note,
            communication_id=escalation.communication_id,
            message_body=(
                _message_body_for_display(communication) if communication else None
            ),
            created_at=escalation.created_at,
        )
        if communication is None:
            unassigned.append(escalation_read)
            continue
        contact = _message_contact(communication) or _contact(
            manager, communication.channel
        )
        key = (
            _supplier_key(supplier.id, communication.channel)
            if supplier
            else _contact_key(communication.channel, contact)
        )
        conversation = conversations.get(key)
        if conversation is None:
            conversation = _new_conversation(
                supplier=supplier,
                manager=manager,
                channel=communication.channel,
                contact=contact,
                recipient_status=None,
            )
            conversations[key] = conversation
        conversation.escalations.append(escalation_read)

    quotations_by_supplier: dict[int, list[Quotation]] = {}
    for quotation in quotations:
        if quotation.manager is None:
            continue
        quotations_by_supplier.setdefault(
            quotation.manager.supplier_id, []
        ).append(quotation)

    for key, conversation in conversations.items():
        supplier_quotations = (
            quotations_by_supplier.get(conversation.supplier_id, [])
            if conversation.supplier_id is not None
            else []
        )
        if not supplier_quotations:
            # Компания написала, но условий в письме не было: вопрос про
            # грейд котировки не создаёт. «Ответа нет» тут неправда.
            if key in answered:
                conversation.data_collection_status = "collecting"
            continue
        progress = accumulate_quotations(supplier_quotations)
        missing = list(
            dict.fromkeys(
                [
                    *progress.completeness.missing_fields,
                    *progress.completeness.low_confidence_fields,
                ]
            )
        )
        conversation.missing_quote_fields = missing
        conversation.data_collection_status = (
            "complete" if progress.completeness.is_complete else "collecting"
        )

    for conversation in conversations.values():
        if any(
            escalation.status != EscalationStatus.RESOLVED.value
            for escalation in conversation.escalations
        ):
            conversation.data_collection_status = "needs_human"

    def sort_key(item: SupplierConversationRead) -> tuple[int, float]:
        has_active = any(
            escalation.status != EscalationStatus.RESOLVED.value
            for escalation in item.escalations
        )
        last_message = (
            item.last_message_at.timestamp() if item.last_message_at else 0.0
        )
        return (int(has_active), last_message)

    return CommunicationOverviewRead(
        conversations=sorted(conversations.values(), key=sort_key, reverse=True),
        unassigned_escalations=unassigned,
    )
