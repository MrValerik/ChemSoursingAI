"""Read-only grouping of the shared mailbox by exact correspondent address.

Grouping never changes RFQ/manager ownership or the RFC reply headers. Existing
mail is included without a migration, including messages outside list filters.
"""

from __future__ import annotations

from datetime import date
from email.utils import parseaddr

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.communication import Communication
from app.models.enums import Channel, CommDirection
from app.schemas.communication import (
    MailboxMessageRead,
    MailboxThreadListRead,
    MailboxThreadRead,
    MailboxThreadDetailRead,
)


def mailbox_criteria(*, folder="all", date_from=None, date_to=None, query=None):
    if date_from and date_to and date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    criteria = [Communication.channel == Channel.EMAIL]
    if folder == "inbox":
        criteria.append(Communication.direction == CommDirection.INBOUND)
    elif folder == "sent":
        criteria.append(Communication.direction == CommDirection.OUTBOUND)
    elif folder == "unresolved":
        criteria.extend([
            Communication.direction == CommDirection.INBOUND,
            Communication.rfq_id.is_(None),
        ])
    effective_date = func.coalesce(Communication.message_at, Communication.created_at)
    if date_from:
        criteria.append(func.date(effective_date) >= date_from)
    if date_to:
        criteria.append(func.date(effective_date) <= date_to)
    clean_query = (query or "").strip().casefold()
    if clean_query:
        pattern = f"%{clean_query}%"
        criteria.append(or_(
            func.lower(Communication.subject).like(pattern),
            func.lower(Communication.body).like(pattern),
            func.lower(Communication.from_address).like(pattern),
            func.lower(Communication.to_address).like(pattern),
        ))
    return criteria


def mailbox_message_read(message: Communication) -> MailboxMessageRead:
    return MailboxMessageRead(
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
        rfq_id=message.rfq_id,
        manager_id=message.manager_id,
        is_unresolved=(
            message.direction == CommDirection.INBOUND and message.rfq_id is None
        ),
        message_at=message.message_at or message.created_at,
    )


def _correspondent(message) -> tuple[str, str | None]:
    raw = (
        message.from_address
        if message.direction == CommDirection.INBOUND
        else message.to_address
    )
    address = parseaddr(raw or "")[1].strip().casefold()
    if not address or "@" not in address:
        # Missing/malformed addresses must not join unrelated letters together.
        return f"message:{message.id}", None
    return f"email:{address}", address


def _address_index(db: Session, criteria):
    # Scan only lightweight metadata, not every message body/attachment. The
    # match flag is evaluated in SQL so filtering does not truncate history.
    effective_date = func.coalesce(Communication.message_at, Communication.created_at)
    return db.execute(
        select(
            Communication.id,
            Communication.direction,
            Communication.from_address,
            Communication.to_address,
            Communication.rfq_id,
            and_(*criteria).label("matches"),
        )
        .where(Communication.channel == Channel.EMAIL)
        .order_by(effective_date.desc(), Communication.id.desc())
    )


def list_mailbox_threads(
    db: Session,
    *,
    folder: str = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> MailboxThreadListRead:
    criteria = mailbox_criteria(
        folder=folder, date_from=date_from, date_to=date_to, query=query,
    )
    groups: dict[str, dict] = {}
    matching_keys: list[str] = []
    total_messages = 0
    for row in _address_index(db, criteria):
        key, address = _correspondent(row)
        group = groups.setdefault(key, {
            "key": key, "correspondent": address, "message_count": 0,
            "matched_count": 0, "unresolved_count": 0, "rfq_ids": set(),
        })
        group["message_count"] += 1
        if row.direction == CommDirection.INBOUND and row.rfq_id is None:
            group["unresolved_count"] += 1
        if row.rfq_id is not None:
            group["rfq_ids"].add(row.rfq_id)
        if row.matches:
            if group["matched_count"] == 0:
                matching_keys.append(key)
                group["latest_id"] = row.id
            group["matched_count"] += 1
            total_messages += 1

    page = [groups[key] for key in matching_keys[offset:offset + limit]]
    latest_ids = [group["latest_id"] for group in page]
    latest = {
        message.id: message
        for message in db.scalars(
            select(Communication).where(Communication.id.in_(latest_ids))
        )
    } if latest_ids else {}
    return MailboxThreadListRead(
        items=[MailboxThreadRead(
            key=group["key"],
            correspondent=group["correspondent"],
            message_count=group["message_count"],
            matched_count=group["matched_count"],
            unresolved_count=group["unresolved_count"],
            rfq_ids=sorted(group["rfq_ids"]),
            latest_message=mailbox_message_read(latest[group["latest_id"]]),
        ) for group in page],
        total=len(matching_keys),
        total_messages=total_messages,
    )


def get_mailbox_thread(
    db: Session,
    *,
    message_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> MailboxThreadDetailRead:
    anchor = db.get(Communication, message_id)
    if anchor is None or anchor.channel != Channel.EMAIL:
        raise LookupError("Письмо не найдено")
    key, address = _correspondent(anchor)
    ids = [
        row.id for row in _address_index(db, mailbox_criteria())
        if _correspondent(row)[0] == key
    ]
    start = 0
    if before_id is not None:
        if before_id not in ids:
            raise ValueError("Письмо для продолжения не принадлежит этой переписке")
        start = ids.index(before_id) + 1
    page_ids = ids[start:start + limit]
    messages = {
        message.id: message
        for message in db.scalars(
            select(Communication).where(Communication.id.in_(page_ids))
        )
    } if page_ids else {}
    return MailboxThreadDetailRead(
        key=key,
        correspondent=address,
        items=[mailbox_message_read(messages[id_]) for id_ in reversed(page_ids)],
        total=len(ids),
        next_before_id=(
            page_ids[-1] if page_ids and start + len(page_ids) < len(ids) else None
        ),
    )
