"""Эндпоинты поставщиков и рассылки RFQ (разделы 9–10 UI/UX-плана).

Подбор кандидатов сейчас идёт из реестра поставщиков; веб-сорсинг открытых
источников появится на этапе интеграций (функция 3 ТЗ). Отправка по каналам —
демо-режим до подключения Email/WhatsApp-коннекторов (функция 4 ТЗ).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import RfqRecipient, Supplier, User
from app.models.enums import (
    Channel,
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
    """«Разослать выбранным»: все получатели в очереди переходят в «отправлено».

    Демо-режим: реальная отправка появится с Email/WhatsApp-коннекторами;
    тогда же статусы продолжат путь отправлено → доставлено → прочитано.
    """
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
    for r in queued:
        r.status = DispatchStatus.SENT
        r.note = (
            "отправлен шаблон (демо)"
            if r.channel == Channel.WHATSAPP
            else "отправлено (демо: канал не подключён)"
        )
    if rfq.status in (RFQStatus.DRAFT, RFQStatus.VERIFIED):
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
