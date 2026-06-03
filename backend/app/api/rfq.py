"""Эндпоинты RFQ: предпросмотр (без сохранения), создание, чтение, список."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.rfq import RFQ
from app.schemas.rfq import RFQCreate, RFQListItem, RFQRead
from app.services.rfq_builder import (
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)
from app.services.rfq_service import create_rfq, render_rfq_text

router = APIRouter(prefix="/rfq", tags=["rfq"])


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
def preview_rfq(req: RFQGenerateRequest) -> dict:
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
    db: Session = Depends(get_db),
) -> RFQRead:
    """Создаёт RFQ: верификация CAS, генерация текста, сохранение."""
    try:
        rfq = create_rfq(db, data, verify=verify)
    except UnsupportedIncotermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_read(rfq)


@router.get("/{rfq_id}", response_model=RFQRead)
def get(rfq_id: int, db: Session = Depends(get_db)) -> RFQRead:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return _to_read(rfq)


@router.get("", response_model=list[RFQListItem])
def list_rfqs(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RFQ]:
    stmt = select(RFQ).order_by(RFQ.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def _to_read(rfq: RFQ) -> RFQRead:
    """Сериализует RFQ + добавляет сгенерированный текст письма."""
    read = RFQRead.model_validate(rfq)
    subject, body = render_rfq_text(rfq)
    read.rfq_subject = subject
    read.rfq_body = body
    return read
