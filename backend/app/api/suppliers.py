"""Эндпоинты поставщиков и рассылки RFQ (разделы 9–10 UI/UX-плана).

Email и WhatsApp работают в безопасном demo-режиме либо реально после явного
включения администратором.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    PurchaseHistoryEntry,
    Quotation,
    RFQ,
    RfqRecipient,
    RfqSupplierLink,
    SearchRun,
    Supplier,
    SupplierDocument,
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
from app.schemas.quotation import PurchaseHistoryRead
from app.schemas.supplier import (
    RecipientRead,
    RecipientsSelect,
    SupplierContact,
    SupplierContactCreate,
    SupplierCreate,
    SupplierExclusionUpdate,
    SupplierQualificationUpdate,
    SupplierRead,
    SupplierRequestLink,
    SupplierUpdate,
)
from app.services.search_trace import utc_now
from app.services.integration_settings import (
    effective_email_settings,
    effective_whatsapp_settings,
)
from app.services.rfq_service import render_rfq_text
from app.services.quotation_service import purchase_history_read
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
    read.verified_by_name = s.verified_by.full_name if s.verified_by else None
    read.channels = _supplier_channels(s)
    read.contacts = [SupplierContact.model_validate(m) for m in s.managers]
    read.contacts_count = len(s.managers)
    read.linked_requests = linked_requests or []
    read.request_count = len(read.linked_requests)
    read.has_coa = has_coa
    read.has_tds = has_tds
    return read


@router.get("/suppliers", response_model=list[SupplierRead])
def list_suppliers(db: Session = Depends(get_db)) -> list[SupplierRead]:
    """Глобальный реестр компаний с закупочными метриками."""
    stmt = (
        select(Supplier)
        .options(joinedload(Supplier.managers), joinedload(Supplier.verified_by))
        .order_by(Supplier.company)
    )
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
                RfqSupplierLink.status,
            )
            .join(RFQ, RFQ.id == RfqSupplierLink.rfq_id)
            .where(RfqSupplierLink.supplier_id.in_(supplier_ids))
        ).all()
        for supplier_id, rfq_id, name, cas, link_status in candidate_rows:
            linked[supplier_id][rfq_id] = SupplierRequestLink(
                rfq_id=rfq_id,
                name=name,
                cas=cas,
                excluded=link_status == LINK_EXCLUDED,
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
            existing_link = linked[supplier_id].get(rfq_id)
            linked[supplier_id][rfq_id] = SupplierRequestLink(
                rfq_id=rfq_id,
                name=name,
                cas=cas,
                excluded=bool(existing_link and existing_link.excluded),
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


@router.get(
    "/suppliers/{supplier_id}/purchase-history",
    response_model=list[PurchaseHistoryRead],
)
def supplier_purchase_history(
    supplier_id: int,
    db: Session = Depends(get_db),
) -> list[PurchaseHistoryRead]:
    """История итогов, в которых выбран этот реальный поставщик."""
    if db.get(Supplier, supplier_id) is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    entries = db.scalars(
        select(PurchaseHistoryEntry)
        .options(joinedload(PurchaseHistoryEntry.actor))
        .where(PurchaseHistoryEntry.supplier_id == supplier_id)
        .order_by(
            PurchaseHistoryEntry.created_at.desc(),
            PurchaseHistoryEntry.id.desc(),
        )
    ).all()
    return [purchase_history_read(entry) for entry in entries]


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
        if data.qualification_status == "verified":
            supplier.verified_by = user
            supplier.last_checked_at = utc_now()
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


def _supplier_for_edit(db: Session, supplier_id: int, user: User) -> Supplier:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    return supplier


def _supplier_with_links(db: Session, supplier: Supplier) -> SupplierRead:
    rows = db.execute(
        select(RFQ.id, RFQ.name, RFQ.cas, RfqSupplierLink.status)
        .join(RfqSupplierLink, RfqSupplierLink.rfq_id == RFQ.id)
        .where(RfqSupplierLink.supplier_id == supplier.id)
    ).all()
    return _to_supplier_read(
        supplier,
        linked_requests=[
            SupplierRequestLink(
                rfq_id=rfq_id,
                name=name,
                cas=cas,
                excluded=link_status == LINK_EXCLUDED,
            )
            for rfq_id, name, cas, link_status in rows
        ],
    )


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Исправляет вручную поддерживаемые поля строки глобального реестра."""
    supplier = _supplier_for_edit(db, supplier_id, user)
    previous_status = supplier.qualification_status
    changes = data.model_dump(exclude_unset=True)
    if "company" in changes:
        if changes["company"] is None:
            raise HTTPException(
                status_code=422, detail="Название компании не может быть пустым"
            )
        key = company_key(changes["company"])
        if key:
            duplicate = db.scalar(
                select(Supplier.id).where(
                    Supplier.company_key == key,
                    Supplier.id != supplier.id,
                )
            )
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Компания с таким названием уже есть в реестре",
                )
        changes["company_key"] = key
    for field, value in changes.items():
        setattr(supplier, field, value)
    if "qualification_status" in changes:
        supplier.last_checked_at = datetime.now(timezone.utc)
        if changes["qualification_status"] != previous_status:
            supplier.verified_by = (
                user if changes["qualification_status"] == "verified" else None
            )
    db.commit()
    db.refresh(supplier)
    return _supplier_with_links(db, supplier)


def _supplier_has_history(db: Session, supplier_id: int) -> bool:
    direct_links = (
        db.scalar(
            select(RfqSupplierLink.id)
            .where(RfqSupplierLink.supplier_id == supplier_id)
            .limit(1)
        )
        or db.scalar(
            select(RfqRecipient.id)
            .where(RfqRecipient.supplier_id == supplier_id)
            .limit(1)
        )
        or db.scalar(
            select(SupplierDocument.id)
            .where(SupplierDocument.supplier_id == supplier_id)
            .limit(1)
        )
    )
    if direct_links:
        return True
    communication_id = db.scalar(
        select(Communication.id)
        .join(Manager, Manager.id == Communication.manager_id)
        .where(Manager.supplier_id == supplier_id)
        .limit(1)
    )
    quotation_id = db.scalar(
        select(Quotation.id)
        .join(Manager, Manager.id == Quotation.manager_id)
        .where(Manager.supplier_id == supplier_id)
        .limit(1)
    )
    return communication_id is not None or quotation_id is not None


@router.delete(
    "/suppliers/{supplier_id}", status_code=204, response_class=Response
)
def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Удаляет только неиспользованную строку, не стирая историю закупок."""
    supplier = _supplier_for_edit(db, supplier_id, user)
    if _supplier_has_history(db, supplier.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Поставщик уже связан с запросом, перепиской или документами. "
                "Чтобы сохранить историю, измените его статус на «Отклонён»."
            ),
        )
    db.delete(supplier)
    db.commit()
    return Response(status_code=204)


def _is_excluded_for_rfq(db: Session, *, rfq_id: int, supplier_id: int) -> bool:
    link = db.scalars(
        select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == rfq_id,
            RfqSupplierLink.supplier_id == supplier_id,
        )
    ).first()
    return link is not None and link.status == LINK_EXCLUDED


@router.post(
    "/suppliers/{supplier_id}/contacts",
    response_model=SupplierRead,
    status_code=201,
)
def add_supplier_contact(
    supplier_id: int,
    data: SupplierContactCreate,
    rfq_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Контакт, вписанный закупщиком с сайта компании.

    Поиск читает страницу машиной и на трёх преградах останавливается:
    адрес подменён заглушкой от спам-ботов, вместо адреса форма, компанию
    назвала площадка и своей страницы у нас нет. Человек эти преграды
    проходит — открывает сайт и переносит адрес сюда, после чего компания
    получает канал и её можно включить в рассылку.
    """
    supplier = _supplier_for_edit(db, supplier_id, user)
    refusal = data.refusal()
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)

    existing = db.scalars(
        select(Manager).where(Manager.supplier_id == supplier.id)
    ).all()
    if data.email and any(
        (manager.email or "").casefold() == data.email.casefold()
        for manager in existing
    ):
        raise HTTPException(status_code=409, detail="Такой адрес у компании уже есть")
    if data.whatsapp and any(
        (manager.whatsapp or "").strip() == data.whatsapp
        for manager in existing
    ):
        raise HTTPException(status_code=409, detail="Такой номер у компании уже есть")

    substance: str | None = None
    if rfq_id is not None:
        rfq = db.get(RFQ, rfq_id)
        if rfq is None or rfq.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Запрос не найден")
        substance = rfq.name

    db.add(
        Manager(
            supplier_id=supplier.id,
            full_name=data.full_name,
            email=data.email,
            whatsapp=data.whatsapp,
            offered_substances=[substance] if substance else None,
        )
    )
    # Преграду не стираем. Она говорит про наши источники — «своей
    # страницы компании у нас нет, её назвала площадка», — и от того, что
    # человек вписал адрес, это не перестало быть правдой. Показывается
    # она только там, где канала нет, так что живому адресу не помешает, а
    # если вписанный контакт потом уберут, объяснение вернётся на место.
    db.commit()
    db.refresh(supplier)
    return _supplier_with_links(db, supplier)


@router.delete(
    "/suppliers/{supplier_id}/contacts/{contact_id}",
    response_model=SupplierRead,
)
def remove_supplier_contact(
    supplier_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Убрать контакт: без этого опечатка в адресе неисправима.

    Письмо по ошибочному адресу уходит постороннему человеку, а вписывают
    адрес руками — значит, и стереть его надо уметь руками.
    """
    supplier = _supplier_for_edit(db, supplier_id, user)
    manager = db.get(Manager, contact_id)
    if manager is None or manager.supplier_id != supplier.id:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    if manager.communications:
        # По этому адресу уже писали, и переписка на него ссылается.
        raise HTTPException(
            status_code=409,
            detail="По этому контакту уже шла переписка — его нельзя убрать",
        )
    db.delete(manager)
    db.commit()
    db.refresh(supplier)
    return _supplier_with_links(db, supplier)


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


# Статус связи «запрос ↔ компания». Поле было заведено сразу, но до сих пор
# всегда хранило «candidate»: отказаться от компании в рамках одного запроса
# было нечем.
LINK_CANDIDATE = "candidate"
LINK_EXCLUDED = "excluded"


def _writable(user: User) -> None:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")


@router.post(
    "/suppliers/{supplier_id}/qualification", response_model=SupplierRead
)
def set_supplier_qualification(
    supplier_id: int,
    payload: SupplierQualificationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Решение человека о компании: подтвердить, отправить на проверку,
    вернуть в кандидаты или исключить из реестра.

    Проверка ИИ-агентом заводит только кандидата и контрагента не
    подтверждает — подтверждает человек, и до сих пор ему было нечем.
    """
    _writable(user)
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    supplier.qualification_status = payload.status
    # Решение человека и есть проверка: дата в карточке должна показывать
    # её, а не последний машинный прогон.
    supplier.last_checked_at = datetime.now(timezone.utc)
    supplier.verified_by = user if payload.status == "verified" else None
    db.commit()
    db.refresh(supplier)
    return _supplier_with_links(db, supplier)


@router.post(
    "/rfq/{rfq_id}/suppliers/{supplier_id}/exclusion",
    response_model=SupplierRead,
)
def set_supplier_exclusion(
    rfq_id: int,
    supplier_id: int,
    payload: SupplierExclusionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierRead:
    """Отказ от компании в рамках одного запроса.

    «Нашли не то вещество» не значит «это не поставщик»: по другому запросу
    та же компания может подойти. Поэтому отказ живёт в связке с запросом и
    реестр не трогает.
    """
    _writable(user)
    _get_rfq(db, rfq_id)
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    link = db.scalars(
        select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == rfq_id,
            RfqSupplierLink.supplier_id == supplier_id,
        )
    ).first()
    status = LINK_EXCLUDED if payload.excluded else LINK_CANDIDATE
    if link is None:
        # Связи может не быть у компании, добавленной руками: заводим её,
        # иначе отказ некуда записать.
        db.add(
            RfqSupplierLink(
                rfq_id=rfq_id, supplier_id=supplier_id, status=status
            )
        )
    else:
        link.status = status
    if payload.excluded:
        # Исключённая компания не должна остаться в очереди рассылки: это
        # ровно тот случай, когда письмо уходит тому, кого вычеркнули.
        for recipient in db.scalars(
            select(RfqRecipient).where(
                RfqRecipient.rfq_id == rfq_id,
                RfqRecipient.supplier_id == supplier_id,
                RfqRecipient.status == DispatchStatus.QUEUED,
            )
        ).all():
            db.delete(recipient)
    db.commit()
    db.refresh(supplier)
    return _supplier_with_links(db, supplier)


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
        supplier = db.get(Supplier, item.supplier_id)
        if supplier is None:
            raise HTTPException(
                status_code=404, detail=f"Поставщик {item.supplier_id} не найден"
            )
        # Отказ человека должен что-то значить. Раньше исключённая компания
        # спокойно попадала в получатели: статус в реестре не читал никто.
        if supplier.qualification_status == "rejected":
            raise HTTPException(
                status_code=422,
                detail=f"Компания «{supplier.company}» исключена из реестра",
            )
        if _is_excluded_for_rfq(db, rfq_id=rfq_id, supplier_id=supplier.id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Компания «{supplier.company}» вычеркнута из этого запроса"
                ),
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
            recipient.note = "отправлено (демо; Email выключен)"
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
                recipient.note = None
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
        recipient.note = None
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
