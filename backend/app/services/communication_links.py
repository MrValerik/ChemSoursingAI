"""Явные many-to-many связи переписки с RFQ без догадок по названиям."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.communication import Communication
from app.models.communication_rfq import CommunicationRfqLink


def communication_linked_to_rfq(rfq_id: int):
    """SQL-условие читает новые связи и старую совместимую колонку."""

    return or_(
        Communication.rfq_id == rfq_id,
        Communication.rfq_links.any(CommunicationRfqLink.rfq_id == rfq_id),
    )


def link_communication_to_rfqs(
    db: Session,
    *,
    communication: Communication,
    rfq_ids: list[int],
) -> list[int]:
    """Привязывает сообщение только к явно переданным уникальным RFQ."""

    normalized_ids = list(dict.fromkeys(rfq_id for rfq_id in rfq_ids if rfq_id > 0))
    if not normalized_ids:
        raise ValueError("Нужно выбрать хотя бы один RFQ")
    db.flush()
    if communication.rfq_id is None:
        # Временная колонка совместимости остаётся первичной карточкой до
        # перевода всех Email/WhatsApp путей на таблицу связей.
        communication.rfq_id = normalized_ids[0]
    existing_ids = {link.rfq_id for link in communication.rfq_links}
    for rfq_id in normalized_ids:
        if rfq_id not in existing_ids:
            communication.rfq_links.append(CommunicationRfqLink(rfq_id=rfq_id))
    return normalized_ids
