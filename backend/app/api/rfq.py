"""Эндпоинт предпросмотра/генерации RFQ."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.rfq_builder import (
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)

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
    """Генерирует стандартизированный RFQ (тема, текст, структура полей)."""
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
