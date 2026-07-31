"""Эндпоинты котировок и сводной таблицы по RFQ."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.schemas.quotation import QuotationCreate, QuotationRead, SummaryRow
from app.services.quotation_service import build_summary, create_quotation

router = APIRouter(tags=["quotations"], dependencies=[Depends(get_current_user)])


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
