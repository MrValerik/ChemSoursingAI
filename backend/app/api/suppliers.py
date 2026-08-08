"""Эндпоинты поставщиков и рассылки RFQ (разделы 9–10 UI/UX-плана).

Email и WhatsApp работают в безопасном demo-режиме либо реально после явного
включения администратором.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
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
from app.core.db import get_db
from app.models import (
    Communication,
    Quotation,
    RFQ,
    RfqRecipient,
    RfqSupplierLink,
    SearchRun,
    Supplier,
    User,
)
from app.models.enums import (
    Channel,
    CommDirection,
    DispatchStatus,
    RFQStatus,
    UserRole,
)
from app.models.manager import Manager
from app.schemas.supplier import (
    RecipientRead,
    RecipientsSelect,
    SupplierCreate,
    SupplierRead,
    SupplierRequestLink,
)
from app.services.search_trace import utc_now
from app.services.integration_settings import (
    effective_email_settings,
    effective_whatsapp_settings,
)
from app.services.rfq_service import render_rfq_text
from app.services.supplier_registry import company_key

router = APIRouter(tags=["suppliers"], dependencies=[Depends(get_current_user)])


def _supplier_channels(s: Supplier) -> list[Channel]:
    channels: set[Channel] = set()
    for m in s.managers:
        if m.email:
            channels.add(Channel.EMAIL)
        if m.whatsapp:
            channels.add(Channel.WHATSAPP)
    return sorted(channels, key=lambda c: c.value)


def _to_supplier_read(
    s: Supplier,
    *,
    linked_requests: list[SupplierRequestLink] | None = None,
    has_coa: bool = False,
    has_tds: bool = False,
) -> SupplierRead:
    read = SupplierRead.model_validate(s)
    read.channels = _supplier_channels(s)
    read.contacts_count = len(s.managers)
    read.linked_requests = linked_requests or []
    read.request_count = len(read.linked_requests)
    read.has_coa = has_coa
    read.has_tds = has_tds
    return read


@router.get("/suppliers", response_model=list[SupplierRead])
def list_suppliers(db: Session = Depends(get_db)) -> list[SupplierRead]:
    """Глобальный реестр компаний с закупочными метриками."""
    stmt = select(Supplier).options(joinedload(Supplier.managers)).order_by(Supplier.company)
    suppliers = db.scalars(stmt).unique().all()
    supplier_ids = [supplier.id for supplier in suppliers]
    linked: dict[int, dict[int, SupplierRequestLink]] = {
        supplier_id: {} for supplier_id in supplier_ids
    }
    documents: dict[int, dict[str, bool]] = {
        supplier_id: {"has_coa": False, "has_tds": False}
        for supplier_id in supplier_ids
    }
    if supplier_ids:
        candidate_rows = db.execute(
            select(
                RfqSupplierLink.supplier_id,
                RFQ.id,
                RFQ.name,
                RFQ.cas,
            )
            .join(RFQ, RFQ.id == RfqSupplierLink.rfq_id)
            .where(RfqSupplierLink.supplier_id.in_(supplier_ids))
        ).all()
        for supplier_id, rfq_id, name, cas in candidate_rows:
            linked[supplier_id][rfq_id] = SupplierRequestLink(
                rfq_id=rfq_id,
                name=name,
                cas=cas,
            )

        recipient_rows = db.execute(
            select(
                RfqRecipient.supplier_id,
                RFQ.id,
                RFQ.name,
                RFQ.cas,
            )
            .join(RFQ, RFQ.id == RfqRecipient.rfq_id)
            .where(RfqRecipient.supplier_id.in_(supplier_ids))
        ).all()
        for supplier_id, rfq_id, name, cas in recipient_rows:
            linked[supplier_id][rfq_id] = SupplierRequestLink(
                rfq_id=rfq_id,
                name=name,
                cas=cas,
            )

        quotation_rows = db.execute(
            select(
                Manager.supplier_id,
                Quotation.has_coa,
                Quotation.has_tds,
            )
            .join(Manager, Manager.id == Quotation.manager_id)
            .where(Manager.supplier_id.in_(supplier_ids))
        ).all()
        for supplier_id, has_coa, has_tds in quotation_rows:
            documents[supplier_id]["has_coa"] |= bool(has_coa)
            documents[supplier_id]["has_tds"] |= bool(has_tds)

    return [
        _to_supplier_read(
            supplier,
            linked_requests=list(linked[supplier.id].values()),
            **documents[supplier.id],
        )
        for supplier in suppliers
    ]


@router.post("/suppliers", response_model=SupplierRead, status_code=201)
def add_supplier(
    data: SupplierCreate,
    rfq_id: int | None = Query(default=None, ge=1),
    search_run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Ручное добавление поставщика с контактом (раздел 9)."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq: RFQ | None = None
    if rfq_id is not None:
        rfq = db.get(RFQ, rfq_id)
        can_see_all = user.role in {
            UserRole.HEAD,
            UserRole.ADMIN,
            UserRole.AUDITOR,
        }
        if rfq is None or rfq.deleted_at is not None or (
            not can_see_all
            and rfq.owner_id is not None
            and rfq.owner_id != user.id
        ):
            raise HTTPException(status_code=404, detail="Запрос не найден")
    search_run: SearchRun | None = None
    if search_run_id is not None:
        search_run = db.get(SearchRun, search_run_id)
        if (
            rfq is None
            or search_run is None
            or search_run.rfq_id != rfq.id
        ):
            raise HTTPException(status_code=422, detail="Поиск не связан с запросом")

    supplier = None
    source_is_url = bool(
        data.source
        and data.source.lower().startswith(("http://", "https://"))
    )
    if source_is_url:
        supplier = db.scalar(
            select(Supplier)
            .where(Supplier.source == data.source)
            .options(joinedload(Supplier.managers))
            .limit(1)
        )
    if supplier is None:
        supplier = Supplier(
            company=data.company.strip(),
            company_key=company_key(data.company),
            country=data.country,
            type=data.type,
            reputation=data.reputation,
            source=data.source or "добавлен вручную",
            qualification_status=data.qualification_status,
            evidence_score=data.evidence_score,
            certificates=data.certificates,
            last_checked_at=utc_now() if data.evidence_score is not None else None,
        )
        if data.email or data.whatsapp:
            supplier.managers.append(
                Manager(email=data.email, whatsapp=data.whatsapp)
            )
        db.add(supplier)
        db.flush()
    elif data.evidence_score is not None:
        supplier.evidence_score = max(
            supplier.evidence_score or 0,
            data.evidence_score,
        )
        supplier.last_checked_at = utc_now()

    if rfq is not None:
        link = db.scalar(
            select(RfqSupplierLink).where(
                RfqSupplierLink.rfq_id == rfq.id,
                RfqSupplierLink.supplier_id == supplier.id,
            )
        )
        if link is None:
            db.add(
                RfqSupplierLink(
                    rfq_id=rfq.id,
                    supplier_id=supplier.id,
                    search_run_id=search_run.id if search_run else None,
                    source_url=data.source,
                )
            )
    db.commit()
    db.refresh(supplier)
    linked_requests = (
        [SupplierRequestLink(rfq_id=rfq.id, name=rfq.name, cas=rfq.cas)]
        if rfq is not None
        else []
    )
    return _to_supplier_read(supplier, linked_requests=linked_requests)


def _get_rfq(db: Session, rfq_id: int) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
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
                status_code=404, detail=f"Поставщик {item.supplier_id} не найден"
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
    confirm_external_send: bool = Query(default=False),
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
    email_settings, email_enabled, _ = effective_email_settings(db)
    whatsapp_settings, whatsapp_enabled, _ = effective_whatsapp_settings(db)
    live_email = (
        email_enabled and email_settings.email_delivery_mode == "live"
    )
    email_connector = EmailConnector(email_settings) if live_email else None
    whatsapp_connector = (
        WhatsAppConnector(whatsapp_settings) if whatsapp_enabled else None
    )
    will_send_externally = any(
        (recipient.channel == Channel.EMAIL and live_email)
        or (
            recipient.channel == Channel.WHATSAPP
            and whatsapp_connector is not None
        )
        for recipient in queued
    )
    if will_send_externally and not confirm_external_send:
        raise HTTPException(
            status_code=422,
            detail="Подтвердите реальную внешнюю отправку RFQ",
        )
    subject, body = render_rfq_text(rfq)
    subject = f"[RFQ-{rfq.id}] {subject}"
    sent_any = False

    for recipient in queued:
        if recipient.channel == Channel.WHATSAPP:
            manager = next(
                (item for item in recipient.supplier.managers if item.whatsapp),
                None,
            )
            if whatsapp_connector is None:
                recipient.status = DispatchStatus.SENT
                recipient.note = "отправлено (демо; WhatsApp выключен)"
                db.add(
                    Communication(
                        rfq_id=rfq.id,
                        manager_id=manager.id if manager else None,
                        direction=CommDirection.OUTBOUND,
                        channel=Channel.WHATSAPP,
                        subject=None,
                        body=body,
                        from_address=None,
                        to_address=manager.whatsapp if manager else None,
                        status="demo",
                        thread_id=None,
                        external_id=None,
                        attachments=None,
                    )
                )
                sent_any = True
                continue
            if manager is None:
                recipient.status = DispatchStatus.ERROR
                recipient.note = "у поставщика отсутствует WhatsApp"
                continue
            attempt_key = f"dispatch-{recipient.id}"
            previous_attempt = db.scalar(
                select(Communication).where(
                    Communication.idempotency_key == attempt_key
                )
            )
            if previous_attempt is not None:
                if previous_attempt.status == "sent":
                    recipient.status = DispatchStatus.SENT
                    recipient.note = "WhatsApp Cloud API: отправлено"
                    sent_any = True
                else:
                    recipient.status = DispatchStatus.ERROR
                    recipient.note = (
                        "Предыдущая попытка не повторена во избежание дубля"
                    )
                continue
            communication = Communication(
                rfq_id=rfq.id,
                manager_id=manager.id,
                direction=CommDirection.OUTBOUND,
                channel=Channel.WHATSAPP,
                subject=None,
                body=body,
                from_address=whatsapp_settings.whatsapp_phone_id,
                to_address=manager.whatsapp,
                status="sending",
                thread_id=None,
                external_id=None,
                idempotency_key=attempt_key,
                attachments=None,
            )
            db.add(communication)
            db.commit()
            try:
                message_id = whatsapp_connector.send_text(
                    to_number=manager.whatsapp,
                    body=body,
                )
            except (
                WhatsAppConfigurationError,
                WhatsAppDeliveryError,
            ) as exc:
                recipient.status = DispatchStatus.ERROR
                recipient.note = str(exc)[:255]
                communication.status = "delivery_error"
                db.commit()
                continue
            recipient.status = DispatchStatus.SENT
            recipient.note = "WhatsApp Cloud API: отправлено"
            communication.status = "sent"
            communication.thread_id = message_id
            communication.external_id = message_id
            db.commit()
            sent_any = True
            continue
        manager = next(
            (item for item in recipient.supplier.managers if item.email), None
        )
        if not live_email:
            recipient.status = DispatchStatus.SENT
            recipient.note = "отправлено (демо; SMTP выключен)"
            db.add(
                Communication(
                    rfq_id=rfq.id,
                    manager_id=manager.id if manager else None,
                    direction=CommDirection.OUTBOUND,
                    channel=Channel.EMAIL,
                    subject=subject,
                    body=body,
                    from_address=email_settings.email_from or None,
                    to_address=manager.email if manager else None,
                    status="demo",
                    thread_id=None,
                    external_id=None,
                    attachments=None,
                )
            )
            sent_any = True
            continue

        if manager is None:
            recipient.status = DispatchStatus.ERROR
            recipient.note = "у поставщика отсутствует Email"
            continue
        assert email_connector is not None
        attempt_key = f"dispatch-{recipient.id}"
        previous_attempt = db.scalar(
            select(Communication).where(
                Communication.idempotency_key == attempt_key
            )
        )
        if previous_attempt is not None:
            if previous_attempt.status == "sent":
                recipient.status = DispatchStatus.SENT
                recipient.note = f"SMTP: отправлено на {manager.email}"[:255]
                sent_any = True
            else:
                recipient.status = DispatchStatus.ERROR
                recipient.note = "Предыдущая попытка не повторена во избежание дубля"
            continue
        communication = Communication(
            rfq_id=rfq.id,
            manager_id=manager.id,
            direction=CommDirection.OUTBOUND,
            channel=Channel.EMAIL,
            subject=subject,
            body=body,
            from_address=email_settings.email_from,
            to_address=manager.email,
            status="sending",
            thread_id=None,
            external_id=None,
            idempotency_key=attempt_key,
            attachments=None,
        )
        db.add(communication)
        db.commit()
        try:
            message_id = email_connector.send(
                to_address=manager.email,
                subject=subject,
                body=body,
            )
        except (EmailConfigurationError, EmailDeliveryError) as exc:
            recipient.status = DispatchStatus.ERROR
            recipient.note = str(exc)[:255]
            communication.status = "delivery_error"
            db.commit()
            continue

        recipient.status = DispatchStatus.SENT
        recipient.note = f"SMTP: отправлено на {manager.email}"[:255]
        communication.status = "sent"
        communication.thread_id = message_id
        communication.external_id = message_id
        db.commit()
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
        raise HTTPException(status_code=404, detail="Получатель не найден")
    if rec.status != DispatchStatus.QUEUED:
        raise HTTPException(status_code=422, detail="Уже отправлено — отмена недоступна")
    db.delete(rec)
    db.commit()
