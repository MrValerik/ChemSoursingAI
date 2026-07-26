"""Эндпоинты поставщиков и рассылки RFQ (разделы 9–10 UI/UX-плана).

Email работает в безопасном demo-режиме либо реально через SMTP после явного
включения EMAIL_DELIVERY_MODE=live. WhatsApp пока остаётся демонстрационным.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Communication, RfqRecipient, Supplier, User
from app.models.enums import (
    Channel,
    CommDirection,
    DispatchStatus,
    RFQStatus,
    UserRole,
)
from app.models.manager import Manager
from app.models.rfq import RFQ
from app.schemas.supplier import (
    RecipientRead,
    RecipientsSelect,
    SupplierCreate,
    SupplierRead,
)
from app.services.rfq_service import render_rfq_text

router = APIRouter(tags=["suppliers"], dependencies=[Depends(get_current_user)])


def _supplier_channels(s: Supplier) -> list[Channel]:
    channels: set[Channel] = set()
    for m in s.managers:
        if m.email:
            channels.add(Channel.EMAIL)
        if m.whatsapp:
            channels.add(Channel.WHATSAPP)
    return sorted(channels, key=lambda c: c.value)


def _to_supplier_read(s: Supplier) -> SupplierRead:
    read = SupplierRead.model_validate(s)
    read.channels = _supplier_channels(s)
    return read


@router.get("/suppliers", response_model=list[SupplierRead])
def list_suppliers(db: Session = Depends(get_db)) -> list[SupplierRead]:
    """Реестр поставщиков (кандидаты для рассылки)."""
    stmt = select(Supplier).options(joinedload(Supplier.managers)).order_by(Supplier.company)
    suppliers = db.scalars(stmt).unique().all()
    return [_to_supplier_read(s) for s in suppliers]


@router.post("/suppliers", response_model=SupplierRead, status_code=201)
def add_supplier(
    data: SupplierCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Ручное добавление поставщика с контактом (раздел 9)."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    supplier = Supplier(
        company=data.company.strip(),
        country=data.country,
        type=data.type,
        reputation=data.reputation,
        source=data.source or "добавлен вручную",
    )
    if data.email or data.whatsapp:
        supplier.managers.append(
            Manager(email=data.email, whatsapp=data.whatsapp)
        )
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return _to_supplier_read(supplier)


def _get_rfq(db: Session, rfq_id: int) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return rfq


def _to_recipient_read(r: RfqRecipient) -> RecipientRead:
    read = RecipientRead.model_validate(r)
    read.supplier_company = r.supplier.company if r.supplier else None
    return read


@router.get("/rfq/{rfq_id}/recipients", response_model=list[RecipientRead])
def list_recipients(rfq_id: int, db: Session = Depends(get_db)) -> list[RecipientRead]:
    _get_rfq(db, rfq_id)
    stmt = (
        select(RfqRecipient)
        .options(joinedload(RfqRecipient.supplier))
        .where(RfqRecipient.rfq_id == rfq_id)
        .order_by(RfqRecipient.id)
    )
    return [_to_recipient_read(r) for r in db.scalars(stmt).all()]


@router.post("/rfq/{rfq_id}/recipients", response_model=list[RecipientRead])
def select_recipients(
    rfq_id: int,
    payload: RecipientsSelect,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RecipientRead]:
    """Добавляет выбранных получателей (чекбоксы раздела 9). Идемпотентно."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    _get_rfq(db, rfq_id)
    existing = {
        (r.supplier_id, r.channel)
        for r in db.scalars(
            select(RfqRecipient).where(RfqRecipient.rfq_id == rfq_id)
        ).all()
    }
    for item in payload.items:
        if (item.supplier_id, item.channel) in existing:
            continue
        if db.get(Supplier, item.supplier_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Supplier {item.supplier_id} not found"
            )
        db.add(
            RfqRecipient(
                rfq_id=rfq_id,
                supplier_id=item.supplier_id,
                channel=item.channel,
                status=DispatchStatus.QUEUED,
            )
        )
    db.commit()
    return list_recipients(rfq_id, db)


@router.post("/rfq/{rfq_id}/dispatch", response_model=list[RecipientRead])
def dispatch(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RecipientRead]:
    """Рассылает Email через SMTP либо сохраняет безопасное demo-поведение."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = _get_rfq(db, rfq_id)
    queued = db.scalars(
        select(RfqRecipient).where(
            RfqRecipient.rfq_id == rfq_id,
            RfqRecipient.status == DispatchStatus.QUEUED,
        )
    ).all()
    if not queued:
        raise HTTPException(status_code=422, detail="Нет получателей в очереди")
    settings = get_settings()
    live_email = settings.email_delivery_mode.strip().lower() == "live"
    connector = EmailConnector(settings) if live_email else None
    subject, body = render_rfq_text(rfq)
    subject = f"[RFQ-{rfq.id}] {subject}"
    sent_any = False

    for recipient in queued:
        if recipient.channel != Channel.EMAIL:
            recipient.status = DispatchStatus.SENT
            recipient.note = "отправлен шаблон (демо: WhatsApp не подключён)"
            sent_any = True
            continue
        if not live_email:
            recipient.status = DispatchStatus.SENT
            recipient.note = "отправлено (демо; SMTP выключен)"
            sent_any = True
            continue

        manager = next(
            (item for item in recipient.supplier.managers if item.email), None
        )
        if manager is None:
            recipient.status = DispatchStatus.ERROR
            recipient.note = "у поставщика отсутствует Email"
            continue
        assert connector is not None
        try:
            message_id = connector.send(
                to_address=manager.email,
                subject=subject,
                body=body,
            )
        except (EmailConfigurationError, EmailDeliveryError) as exc:
            recipient.status = DispatchStatus.ERROR
            recipient.note = str(exc)[:255]
            continue

        recipient.status = DispatchStatus.SENT
        recipient.note = f"SMTP: отправлено на {manager.email}"[:255]
        db.add(
            Communication(
                rfq_id=rfq.id,
                manager_id=manager.id,
                direction=CommDirection.OUTBOUND,
                channel=Channel.EMAIL,
                subject=subject,
                body=body,
                from_address=settings.email_from,
                to_address=manager.email,
                status="sent",
                thread_id=message_id,
                external_id=message_id,
                attachments=None,
            )
        )
        sent_any = True

    if sent_any and rfq.status in (RFQStatus.DRAFT, RFQStatus.VERIFIED):
        rfq.status = RFQStatus.SENT
    db.commit()
    return list_recipients(rfq_id, db)


@router.delete("/rfq/{rfq_id}/recipients/{recipient_id}", status_code=204)
def remove_recipient(
    rfq_id: int,
    recipient_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Отмена по получателю — только пока он в очереди (раздел 10)."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rec = db.get(RfqRecipient, recipient_id)
    if rec is None or rec.rfq_id != rfq_id:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if rec.status != DispatchStatus.QUEUED:
        raise HTTPException(status_code=422, detail="Уже отправлено — отмена недоступна")
    db.delete(rec)
    db.commit()
