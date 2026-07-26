"""Поиск кандидатов-поставщиков с доказательствами из открытых источников."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.web_search import search_web
from app.core.db import get_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import PromptTemplate, User

router = APIRouter(prefix="/supplier-search", tags=["supplier-search"])


class SupplierSearchRequest(BaseModel):
    cas: str = Field(..., min_length=3, max_length=20)
    name: str = Field(..., min_length=2, max_length=255)
    country: str | None = Field(default="China", max_length=100)
    additional_instructions: str | None = Field(default=None, max_length=4000)
    limit: int = Field(default=8, ge=1, le=20)


def _fallback_query(data: SupplierSearchRequest) -> str:
    country = f" {data.country}" if data.country else ""
    return f'"{data.name}" "{data.cas}" manufacturer supplier{country} CoA'


@router.post("")
def supplier_search(
    data: SupplierSearchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "supplier_search",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    query = _fallback_query(data)
    fallback_query = query
    ai_query: str | None = None
    ai_used = False
    if prompt:
        try:
            generated = LLMClient().generate_text(
                system_prompt=prompt.system_prompt
                + "\nReturn exactly one web search query and no explanation.",
                user_text=(
                    f"Chemical: {data.name}\nCAS: {data.cas}\n"
                    f"Country: {data.country or 'any'}"
                ),
                additional_instructions=data.additional_instructions,
                # A search query is one short line; a larger budget only adds latency.
                max_tokens=64,
            )
            candidate = generated.strip().strip("`").splitlines()[0].strip()
            if 5 <= len(candidate) <= 500:
                query = candidate
                ai_query = candidate
                ai_used = True
        except LLMUnavailableError:
            pass
    fallback_used = False
    try:
        results = search_web(query, data.limit)
        # An AI-generated query can be too restrictive. Retry once with a
        # deterministic query so an empty first attempt is not the final result.
        if not results and query != fallback_query:
            results = search_web(fallback_query, data.limit)
            query = fallback_query
            fallback_used = True
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Поисковый источник недоступен: {exc}") from exc
    return {
        "query": query,
        "ai_query": ai_query,
        "ai_used": ai_used,
        "fallback_used": fallback_used,
        "results": results,
        "warning": (
            "Результаты являются кандидатами. Статус производителя и документы "
            "необходимо подтвердить по первичному источнику."
        ),
    }
