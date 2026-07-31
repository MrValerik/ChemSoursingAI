"""Эндпоинты извлечения котировки из текста ответа поставщика."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.extraction.pipeline import extract_quote
from app.models.rfq import RFQ
from app.models import User
from app.models.enums import UserRole
from app.schemas.quotation import QuotationCreate, QuotationRead
from app.services.quotation_service import create_quotation
from app.services.prompt_service import get_rfq_prompt_context

router = APIRouter(tags=["extraction"], dependencies=[Depends(get_current_user)])
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _require_rfq_access(user: User, rfq: RFQ) -> None:
    if user.role not in _SEE_ALL_ROLES and rfq.owner_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="Запрос не найден")


class ExtractRequest(BaseModel):
    text: str = Field(..., description="Текст ответа поставщика")
    use_llm: bool = Field(default=True, description="Использовать LLM (иначе только правила)")
    rfq_id: int | None = None
    additional_instructions: str | None = Field(default=None, max_length=4000)


class ExtractToQuotationRequest(ExtractRequest):
    manager_id: int | None = None


@router.post("/extraction/quote")
def extract_preview(
    req: ExtractRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Извлекает котировку без сохранения (предпросмотр). LLM→валидаторы→fallback."""
    system_prompt = None
    saved_instructions = None
    if req.rfq_id is not None:
        rfq = db.get(RFQ, req.rfq_id)
        if rfq is None or rfq.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Запрос не найден")
        _require_rfq_access(user, rfq)
        system_prompt, saved_instructions = get_rfq_prompt_context(db, req.rfq_id)
    result = extract_quote(
        req.text,
        use_llm=req.use_llm,
        system_prompt=system_prompt,
        additional_instructions=req.additional_instructions or saved_instructions,
    )
    return result.to_dict()


@router.post("/rfq/{rfq_id}/extract", response_model=QuotationRead, status_code=201)
def extract_and_store(
    rfq_id: int,
    req: ExtractToQuotationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Извлекает котировку из ответа и сохраняет её в RFQ (с контролем полноты)."""
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    _require_rfq_access(user, rfq)
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    system_prompt, saved_instructions = get_rfq_prompt_context(db, rfq_id)
    q = extract_quote(
        req.text,
        use_llm=req.use_llm,
        system_prompt=system_prompt,
        additional_instructions=req.additional_instructions or saved_instructions,
    )
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
