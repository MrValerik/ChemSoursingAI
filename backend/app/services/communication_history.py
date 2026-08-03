"""Собирает единый обзор переписки RFQ по поставщикам и каналам."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Communication, Escalation, RfqRecipient, Supplier
from app.models.enums import Channel, CommDirection, DispatchStatus, EscalationStatus
from app.models.manager import Manager
from app.schemas.communication import (
    CommunicationEscalationRead,
    CommunicationMessageRead,
    CommunicationOverviewRead,
    SupplierConversationRead,
)


def _contact(manager: Manager | None, channel: Channel) -> str | None:
    if manager is None:
        return None
    return manager.email if channel == Channel.EMAIL else manager.whatsapp


def _message_contact(message: Communication) -> str | None:
    if message.direction == CommDirection.INBOUND:
        return message.from_address
    return message.to_address


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
                joinedload(Communication.manager).joinedload(Manager.supplier)
            )
            .where(Communication.rfq_id == rfq_id)
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

    conversations: dict[tuple[str, str, str], SupplierConversationRead] = {}
    contacts: dict[tuple[Channel, str], tuple[Supplier, Manager]] = {}

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
        elif conversation.manager_id is None and manager is not None:
            conversation.manager_id = manager.id
            conversation.manager_name = manager.full_name
            conversation.contact = contact
        conversation.messages.append(
            CommunicationMessageRead(
                id=message.id,
                direction=message.direction,
                channel=message.channel,
                subject=message.subject,
                body=message.body,
                status=message.status,
                from_address=message.from_address,
                to_address=message.to_address,
                attachments=message.attachments,
                created_at=message.created_at,
            )
        )
        conversation.last_message_at = message.created_at

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
            message_body=communication.body if communication else None,
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
