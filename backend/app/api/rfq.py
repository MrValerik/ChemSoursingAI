"""Эндпоинты RFQ: предпросмотр, создание, чтение, сводный список.

Видимость по ролям (раздел 4 UI/UX-плана): закупщик видит свои запросы,
руководитель/администратор/аудитор — все. Права проверяются на сервере.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import RfqAiSetting, Substance, User
from app.models.enums import EscalationStatus, UserRole
from app.models.escalation import Escalation
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.schemas.rfq import RFQCreate, RFQListItem, RFQRead
from app.services.rfq_builder import (
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)
from app.services.rfq_service import create_rfq, render_rfq_text
from app.services.search_trace import create_search_run

router = APIRouter(prefix="/rfq", tags=["rfq"])

# Роли, видящие все запросы (остальные — только свои).
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _can_see(user: User, rfq: RFQ) -> bool:
    if user.role in _SEE_ALL_ROLES:
        return True
    return rfq.owner_id is None or rfq.owner_id == user.id


class RFQGenerateRequest(BaseModel):
    cas: str = Field(..., examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
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
                    "known_synonyms": (
                        list(selected_substance.synonyms or [])
                        if selected_substance
                        else []
                    ),
                    "excluded_names": (
                        list(selected_substance.excluded_names or [])
                        if selected_substance
                        else []
                    ),
                    "country": country,
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
    if rfq is None or not _can_see(user, rfq):
        raise HTTPException(status_code=404, detail="RFQ not found")
    return _to_read(rfq)


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
        item.has_open_escalation = r.id in escalated
        items.append(item)
    return items


def _to_read(rfq: RFQ) -> RFQRead:
    """Сериализует RFQ + добавляет сгенерированный текст письма."""
    read = RFQRead.model_validate(rfq)
    subject, body = render_rfq_text(rfq)
    read.rfq_subject = subject
    read.rfq_body = body
    read.owner_name = rfq.owner.full_name if rfq.owner else None
    read.substance_preferred_name = (
        rfq.substance.preferred_name if rfq.substance else None
    )
    read.substance_review_status = (
        rfq.substance.review_status if rfq.substance else None
    )
    return read
