"""Поиск кандидатов-поставщиков с доказательствами из открытых источников."""

import json
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
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
    limit: int = Field(default=5, ge=1, le=20)


class SupplierSearchResultInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=1000)
    url: str = Field(..., min_length=8, max_length=4000)
    snippet: str = Field(default="", max_length=8000)

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if urlparse(value).scheme.lower() not in {"http", "https"}:
            raise ValueError("source URL must use http or https")
        return value


class SupplierQualificationRequest(BaseModel):
    cas: str = Field(..., min_length=3, max_length=20)
    name: str = Field(..., min_length=2, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    additional_instructions: str | None = Field(default=None, max_length=4000)
    results: list[SupplierSearchResultInput] = Field(
        ..., min_length=1, max_length=5
    )


EvidenceStatus = Literal["claimed", "not_found", "contradicted"]
SupplierKind = Literal["manufacturer", "distributor", "unknown"]
CasStatus = Literal["confirmed", "mentioned", "not_found", "mismatch"]


class SupplierQualification(BaseModel):
    result_index: int = Field(..., ge=0, le=4)
    company_name: str = Field(..., min_length=1, max_length=255)
    title_ru: str = Field(..., min_length=1, max_length=500)
    summary_ru: str = Field(..., min_length=1, max_length=1200)
    supplier_type: SupplierKind
    cas_status: CasStatus
    gmp_status: EvidenceStatus
    iso_status: EvidenceStatus
    coa_status: EvidenceStatus
    tds_status: EvidenceStatus
    confidence: int = Field(..., ge=0, le=100)
    red_flags: list[str] = Field(default_factory=list, max_length=4)
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)


_QUALIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "result_index": {"type": "integer", "minimum": 0, "maximum": 4},
                    "company_name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 255,
                    },
                    "title_ru": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "summary_ru": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1200,
                    },
                    "supplier_type": {
                        "type": "string",
                        "enum": ["manufacturer", "distributor", "unknown"],
                    },
                    "cas_status": {
                        "type": "string",
                        "enum": ["confirmed", "mentioned", "not_found", "mismatch"],
                    },
                    "gmp_status": {
                        "type": "string",
                        "enum": ["claimed", "not_found", "contradicted"],
                    },
                    "iso_status": {
                        "type": "string",
                        "enum": ["claimed", "not_found", "contradicted"],
                    },
                    "coa_status": {
                        "type": "string",
                        "enum": ["claimed", "not_found", "contradicted"],
                    },
                    "tds_status": {
                        "type": "string",
                        "enum": ["claimed", "not_found", "contradicted"],
                    },
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "red_flags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 500},
                        "maxItems": 4,
                    },
                    "missing_evidence": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 500},
                        "maxItems": 5,
                    },
                },
                "required": [
                    "result_index",
                    "company_name",
                    "title_ru",
                    "summary_ru",
                    "supplier_type",
                    "cas_status",
                    "gmp_status",
                    "iso_status",
                    "coa_status",
                    "tds_status",
                    "confidence",
                    "red_flags",
                    "missing_evidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _fallback_query(data: SupplierSearchRequest) -> str:
    country = f" {data.country}" if data.country else ""
    return f'"{data.name}" "{data.cas}" manufacturer supplier{country} CoA'


def _domain_key(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname.removeprefix("www.") or url


def _deduplicate_results(results: list[dict], limit: int) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for result in results:
        key = _domain_key(result["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
        if len(unique) >= limit:
            break
    return unique


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
        fetch_limit = min(data.limit * 2, 20)
        results = _deduplicate_results(search_web(query, fetch_limit), data.limit)
        # An AI-generated query can be too restrictive. Retry once with a
        # deterministic query so an empty first attempt is not the final result.
        if len(results) < data.limit and query != fallback_query:
            fallback_results = search_web(fallback_query, fetch_limit)
            combined = _deduplicate_results(
                [*results, *fallback_results], data.limit
            )
            fallback_used = len(combined) > len(results) or not results
            results = combined
            if fallback_used:
                query = fallback_query
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


@router.post("/qualify")
def qualify_supplier_results(
    data: SupplierQualificationRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "qualification",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    base_prompt = (
        prompt.system_prompt
        if prompt
        else "Evaluate chemical suppliers using only the supplied evidence."
    )
    system_prompt = (
        base_prompt
        + "\n\nОтветь на русском языке. Для каждого результата верни ровно одну "
        "оценку с тем же result_index. Не считай текст сайта независимым "
        "подтверждением: GMP, ISO, CoA и TDS могут иметь статус claimed только "
        "при явном упоминании, иначе not_found. Статус manufacturer допустим "
        "только при прямом заявлении о собственном производстве или заводе. "
        "Кратко перечисли риски и недостающие доказательства. Не изменяй CAS, "
        "названия компаний и факты источника."
    )
    source_data = {
        "chemical": {
            "name": data.name,
            "cas": data.cas,
            "country": data.country,
            "user_requirements": data.additional_instructions,
        },
        "sources": [
            {
                "result_index": index,
                "title": result.title[:300],
                # Search snippets can be unexpectedly large. Keep the complete
                # original in the API response, but bound the LLM context.
                "snippet": result.snippet[:900],
                "domain": _domain_key(result.url),
            }
            for index, result in enumerate(data.results)
        ],
    }
    try:
        raw = LLMClient().generate_json(
            system_prompt=system_prompt,
            user_text=json.dumps(source_data, ensure_ascii=False),
            schema_name="supplier_qualification",
            json_schema=_QUALIFICATION_SCHEMA,
            max_tokens=768,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Qwen недоступна: {exc}"
        ) from exc

    qualifications: dict[int, SupplierQualification] = {}
    for item in raw.get("results", []) if isinstance(raw, dict) else []:
        try:
            parsed = SupplierQualification.model_validate(item)
        except ValidationError:
            continue
        if parsed.result_index < len(data.results):
            qualifications.setdefault(parsed.result_index, parsed)

    combined_results: list[dict] = []
    for index, source in enumerate(data.results):
        qualification = qualifications.get(index)
        if qualification is None:
            combined_results.append(
                {
                    **source.model_dump(),
                    "result_index": index,
                    "company_name": source.title[:255],
                    "title_ru": source.title,
                    "summary_ru": (
                        "Qwen не вернула корректную структурированную оценку "
                        "для этого результата."
                    ),
                    "supplier_type": "unknown",
                    "cas_status": "not_found",
                    "gmp_status": "not_found",
                    "iso_status": "not_found",
                    "coa_status": "not_found",
                    "tds_status": "not_found",
                    "confidence": 0,
                    "red_flags": ["Автоматическая оценка не получена"],
                    "missing_evidence": ["Требуется ручная проверка источника"],
                }
            )
            continue
        combined_results.append(
            {**source.model_dump(), **qualification.model_dump()}
        )

    return {
        "results": combined_results,
        "prompt_id": prompt.id if prompt else None,
        "prompt_version": prompt.version if prompt else None,
        "warning": (
            "Квалификация предварительная и основана только на фрагментах "
            "поисковой выдачи. Сертификаты и статус производителя требуют "
            "проверки по первичным документам."
        ),
    }
