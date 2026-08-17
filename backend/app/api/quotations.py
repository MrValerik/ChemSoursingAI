"""Эндпоинты котировок и сводной таблицы по RFQ."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.enums import UserRole
from app.models.purchase_decision import PurchaseDecision
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.models.user import User
from app.schemas.quotation import (
    PurchaseDecisionRead,
    PurchaseDecisionUpdate,
    QuotationCreate,
    QuotationRead,
    SummaryRow,
)
from app.services.quotation_service import (
    build_summary,
    create_quotation,
    save_purchase_decision,
)

router = APIRouter(tags=["quotations"], dependencies=[Depends(get_current_user)])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _load_visible_rfq(db: Session, rfq_id: int, user: User) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if (
        rfq is None
        or rfq.deleted_at is not None
        or (
            user.role not in _SEE_ALL_ROLES
            and rfq.owner_id not in (None, user.id)
        )
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


def _decision_read(decision: PurchaseDecision) -> PurchaseDecisionRead:
    result = PurchaseDecisionRead.model_validate(decision, from_attributes=True)
    result.selected_by_name = (
        decision.selected_by.full_name if decision.selected_by else None
    )
    return result


@router.post("/quotations", response_model=QuotationRead, status_code=201)
def create(data: QuotationCreate, db: Session = Depends(get_db)) -> Quotation:
    """Создаёт котировку: контроль полноты + авто-эскалация нестандартных кейсов."""
    rfq = db.get(RFQ, data.rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return create_quotation(db, data)


@router.get("/rfq/{rfq_id}/quotations", response_model=list[QuotationRead])
def list_for_rfq(rfq_id: int, db: Session = Depends(get_db)) -> list[Quotation]:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    stmt = select(Quotation).where(Quotation.rfq_id == rfq_id)
    return list(db.scalars(stmt).all())


@router.get("/rfq/{rfq_id}/summary", response_model=list[SummaryRow])
def summary(rfq_id: int, db: Session = Depends(get_db)) -> list[SummaryRow]:
    """Сводная сравнительная таблица по RFQ (полные котировки — выше)."""
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return build_summary(db, rfq_id)


@router.get(
    "/rfq/{rfq_id}/purchase-decision",
    response_model=PurchaseDecisionRead | None,
)
def get_purchase_decision(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PurchaseDecisionRead | None:
    """Возвращает сохранённый человеком итог выбора предложения."""
    _load_visible_rfq(db, rfq_id, user)
    decision = db.scalar(
        select(PurchaseDecision).where(PurchaseDecision.rfq_id == rfq_id)
    )
    return _decision_read(decision) if decision else None


@router.put(
    "/rfq/{rfq_id}/purchase-decision",
    response_model=PurchaseDecisionRead,
)
def update_purchase_decision(
    rfq_id: int,
    payload: PurchaseDecisionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PurchaseDecisionRead:
    """Сохраняет ручной выбор; заказ, договор и оплата не создаются."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = _load_visible_rfq(db, rfq_id, user)
    try:
        decision = save_purchase_decision(
            db,
            rfq=rfq,
            quotation_id=payload.quotation_id,
            note=payload.note,
            actor=user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _decision_read(decision)
