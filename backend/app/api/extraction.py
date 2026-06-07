"""Эндпоинты извлечения котировки из текста ответа поставщика."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.extraction.pipeline import extract_quote
from app.models.rfq import RFQ
from app.schemas.quotation import QuotationCreate, QuotationRead
from app.services.quotation_service import create_quotation

router = APIRouter(tags=["extraction"], dependencies=[Depends(get_current_user)])


class ExtractRequest(BaseModel):
    text: str = Field(..., description="Текст ответа поставщика")
    use_llm: bool = Field(default=True, description="Использовать LLM (иначе только правила)")


class ExtractToQuotationRequest(ExtractRequest):
    manager_id: int | None = None


@router.post("/extraction/quote")
def extract_preview(req: ExtractRequest) -> dict:
    """Извлекает котировку без сохранения (предпросмотр). LLM→валидаторы→fallback."""
    result = extract_quote(req.text, use_llm=req.use_llm)
    return result.to_dict()


@router.post("/rfq/{rfq_id}/extract", response_model=QuotationRead, status_code=201)
def extract_and_store(
    rfq_id: int,
    req: ExtractToQuotationRequest,
    db: Session = Depends(get_db),
):
    """Извлекает котировку из ответа и сохраняет её в RFQ (с контролем полноты)."""
    if db.get(RFQ, rfq_id) is None:
        raise HTTPException(status_code=404, detail="RFQ not found")

    q = extract_quote(req.text, use_llm=req.use_llm)
    data = QuotationCreate(
        rfq_id=rfq_id,
        manager_id=req.manager_id,
        price=q.price,
        currency=q.currency,
        incoterm=q.incoterm,
        moq=q.moq,
        grade=q.grade,
        payment_terms=q.payment_terms,
        lead_time=q.lead_time,
        has_coa=q.has_coa,
        has_tds=q.has_tds,
        field_confidence=q.field_confidence,
        source_text=req.text,
    )
    return create_quotation(db, data)
