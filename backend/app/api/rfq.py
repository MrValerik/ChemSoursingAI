"""Эндпоинты RFQ: предпросмотр, создание, чтение, сводный список.

Видимость по ролям (раздел 4 UI/UX-плана): закупщик видит свои запросы,
руководитель/администратор/аудитор — все. Права проверяются на сервере.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import RfqAiSetting, Substance, User
from app.models.enums import DispatchStatus, EscalationStatus, UserRole
from app.models.escalation import Escalation
from app.models.quotation import Quotation
from app.models.recipient import RfqRecipient
from app.models.rfq import RFQ
from app.schemas.rfq import (
    RFQCreate,
    RFQListItem,
    RFQMessageDraftUpdate,
    RFQRead,
    RFQTranslationRead,
)
from app.services.communication_testing import (
    CommunicationTestError,
    translate_preview_text,
)
from app.services.rfq_builder import (
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)
from app.services.rfq_service import (
    archive_rfq,
    create_rfq,
    render_rfq_text,
    update_rfq_message_draft,
)
from app.services.search_trace import create_search_run

router = APIRouter(prefix="/rfq", tags=["rfq"])

# Роли, видящие все запросы (остальные — только свои).
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}
_DELETE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN}


def _can_see(user: User, rfq: RFQ) -> bool:
    if user.role in _SEE_ALL_ROLES:
        return True
    return rfq.owner_id is None or rfq.owner_id == user.id


def _can_delete(user: User, rfq: RFQ) -> bool:
    return user.role in _DELETE_ALL_ROLES or rfq.owner_id == user.id


def _merge_names(*groups: list[str] | None) -> list[str]:
    """Объединяет списки названий без повторов, сохраняя порядок."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or []:
            name = raw.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                merged.append(name)
    return merged


class RFQGenerateRequest(BaseModel):
    cas: str | None = Field(default=None, examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
    identification_method: str = "cas"
    analog_reference: str | None = None
    analog_variations: list[str] = Field(default_factory=list)
    specification: str | None = None
    incoterms: list[str] = Field(..., examples=[["CIP", "FCA", "EXW"]])
    purity: str | None = None
    application: str | None = None
    volume: str | None = None
    target_price: float | None = None
    currency: str = "USD"


@router.post("/preview")
def preview_rfq(
    req: RFQGenerateRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Генерирует RFQ без сохранения (для предпросмотра в UI)."""
    try:
        return build_rfq(
            RFQInput(
                cas=req.cas,
                name=req.name,
                identification_method=req.identification_method,
                analog_reference=req.analog_reference,
                analog_variations=req.analog_variations,
                specification=req.specification,
                incoterms=req.incoterms,
                purity=req.purity,
                application=req.application,
                volume=req.volume,
                target_price=req.target_price,
                currency=req.currency,
            )
        )
    except UnsupportedIncotermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=RFQRead, status_code=201)
def create(
    data: RFQCreate,
    verify: bool = Query(default=True, description="Верифицировать CAS через PubChem"),
    start_search: bool = Query(
        default=False,
        description="Сразу поставить поиск поставщиков в очередь",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    """Создаёт RFQ: верификация CAS, генерация текста, сохранение."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    selected_substance = None
    if data.substance_id is not None:
        selected_substance = db.get(Substance, data.substance_id)
        if selected_substance is None:
            raise HTTPException(status_code=422, detail="Вещество не найдено")
        data = data.model_copy(
            update={
                "cas": selected_substance.cas,
                "name": selected_substance.preferred_name,
            }
        )
    try:
        rfq = create_rfq(db, data, verify=verify, owner_id=user.id)
    except UnsupportedIncotermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if data.additional_instructions:
        db.add(
            RfqAiSetting(
                rfq_id=rfq.id,
                additional_instructions=data.additional_instructions.strip(),
            )
        )
    if start_search:
        for country in data.search_countries:
            create_search_run(
                db,
                owner_id=user.id,
                rfq_id=rfq.id,
                input_payload={
                    "cas": rfq.cas,
                    "name": rfq.name,
                    "catalog_preferred_name": (
                        selected_substance.preferred_name
                        if selected_substance
                        else None
                    ),
                    # Отметки закупщика по этому запросу идут вместе с
                    # накопленными в карточке: без CAS-номера якорем
                    # поиска служит название, и именно подтверждённые
                    # названия держат точность в этой ветке.
                    "known_synonyms": _merge_names(
                        selected_substance.synonyms if selected_substance else None,
                        rfq.confirmed_synonyms,
                    ),
                    "excluded_names": _merge_names(
                        selected_substance.excluded_names
                        if selected_substance
                        else None,
                        rfq.excluded_names,
                    ),
                    "catalog_notes": (
                        selected_substance.notes
                        if selected_substance
                        else None
                    ),
                    "country": country,
                    # Способ идентификации и всё, что к нему прилагается.
                    # Форма их собирает, карточка запроса хранит, а поиск
                    # до этой правки не получал: кнопка «Создать запрос и
                    # начать поиск» строила payload вручную и молча теряла
                    # их. Поиск аналога при этом не падал — он выполнялся
                    # как обычный поиск по названию, и по результату это
                    # было незаметно.
                    "identification_method": rfq.identification_method,
                    "analog_reference": rfq.analog_reference,
                    "analog_variations": list(rfq.analog_variations or []),
                    "specification": rfq.specification,
                    "application": rfq.application,
                    "requested_volume": rfq.volume,
                    "additional_instructions": (
                        data.additional_instructions.strip()
                        if data.additional_instructions
                        else None
                    ),
                    "limit": data.supplier_target,
                },
                mode="queued_search",
                status="queued",
            )
    db.commit()
    db.refresh(rfq)
    return _to_read(rfq)


@router.get("/{rfq_id}", response_model=RFQRead)
def get(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    rfq = db.get(RFQ, rfq_id, options=[joinedload(RFQ.owner)])
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return _to_read(rfq)


@router.put("/{rfq_id}/message-draft", response_model=RFQRead)
def update_message_draft(
    rfq_id: int,
    data: RFQMessageDraftUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    """Сохраняет ручной текст первого RFQ или возвращает исходный шаблон."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(
        RFQ,
        rfq_id,
        options=[joinedload(RFQ.owner), joinedload(RFQ.substance)],
    )
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    update_rfq_message_draft(
        db,
        rfq,
        subject=data.subject,
        body=data.body,
    )
    return _to_read(rfq)


@router.post("/{rfq_id}/translation", response_model=RFQTranslationRead)
def translate_rfq(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQTranslationRead:
    """Переводит сохранённый RFQ для просмотра, ничего не изменяя и не отправляя."""
    rfq = db.get(RFQ, rfq_id, options=[joinedload(RFQ.owner)])
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    subject, body = render_rfq_text(rfq)
    try:
        translation = translate_preview_text(f"Subject: {subject}\n\n{body}")
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RFQTranslationRead(translation_ru=translation)


@router.delete("/{rfq_id}", status_code=204, response_class=Response)
def delete_rfq(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Archive a request without destroying its audit history."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or not _can_delete(user, rfq):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    archive_rfq(db, rfq, actor_id=user.id)
    return Response(status_code=204)


@router.get("", response_model=list[RFQListItem])
def list_rfqs(
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RFQListItem]:
    """Сводный список RFQ с числом котировок, полнотой и эскалациями."""
    stmt = (
        select(RFQ)
        .where(RFQ.deleted_at.is_(None))
        .options(joinedload(RFQ.owner))
        .order_by(RFQ.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(
            (RFQ.owner_id == user.id) | (RFQ.owner_id.is_(None))
        )
    rfqs = list(db.scalars(stmt).all())
    ids = [r.id for r in rfqs]
    if not ids:
        return []

    # Агрегаты одной выборкой: котировки (всего/полные) и открытые эскалации.
    quote_rows = db.execute(
        select(
            Quotation.rfq_id,
            func.count(Quotation.id),
            func.sum(case((Quotation.is_complete.is_(True), 1), else_=0)),
        ).where(Quotation.rfq_id.in_(ids)).group_by(Quotation.rfq_id)
    ).all()
    quotes = {rfq_id: (total or 0, int(complete or 0)) for rfq_id, total, complete in quote_rows}

    # Знаменатель охвата: скольким поставщикам RFQ действительно ушёл. Один
    # поставщик может стоять в двух каналах — считаем компании, не отправки.
    recipient_rows = db.execute(
        select(
            RfqRecipient.rfq_id,
            func.count(func.distinct(RfqRecipient.supplier_id)),
        )
        .where(
            RfqRecipient.rfq_id.in_(ids),
            RfqRecipient.status != DispatchStatus.QUEUED,
        )
        .group_by(RfqRecipient.rfq_id)
    ).all()
    recipients = {rfq_id: int(total or 0) for rfq_id, total in recipient_rows}

    esc_rows = db.execute(
        select(Escalation.rfq_id)
        .where(
            Escalation.rfq_id.in_(ids),
            Escalation.status != EscalationStatus.RESOLVED,
        )
        .distinct()
    ).all()
    escalated = {row[0] for row in esc_rows}

    items: list[RFQListItem] = []
    for r in rfqs:
        total, complete = quotes.get(r.id, (0, 0))
        item = RFQListItem.model_validate(r)
        item.owner_name = r.owner.full_name if r.owner else None
        item.n_quotations = total
        item.n_complete = complete
        item.completeness_pct = round(100 * complete / total) if total else 0
        item.n_recipients = recipients.get(r.id, 0)
        item.has_open_escalation = r.id in escalated
        items.append(item)
    return items


def _to_read(rfq: RFQ) -> RFQRead:
    """Сериализует RFQ + добавляет сгенерированный текст письма."""
    read = RFQRead.model_validate(rfq)
    subject, body = render_rfq_text(rfq)
    read.rfq_subject = subject
    read.rfq_body = body
    read.rfq_is_customized = bool(
        rfq.rfq_subject_override and rfq.rfq_body_override
    )
    read.owner_name = rfq.owner.full_name if rfq.owner else None
    read.substance_preferred_name = (
        rfq.substance.preferred_name if rfq.substance else None
    )
    read.substance_review_status = (
        rfq.substance.review_status if rfq.substance else None
    )
    return read
