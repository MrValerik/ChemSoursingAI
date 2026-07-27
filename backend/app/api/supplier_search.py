"""Поиск кандидатов-поставщиков с доказательствами из открытых источников."""

import json
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.web_search import search_web
from app.core.db import get_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import AgentRun, PromptTemplate, SearchRun, User
from app.models.enums import UserRole
from app.services.search_trace import (
    create_search_run,
    finish_agent_run,
    finish_search_attempt,
    finish_search_run,
    start_agent_run,
    start_search_attempt,
)

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
    country_hint: Literal["likely", "possible", "unknown"] = "unknown"

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if urlparse(value).scheme.lower() not in {"http", "https"}:
            raise ValueError("URL источника должен использовать http или https")
        return value


class SupplierQualificationRequest(BaseModel):
    search_run_id: int | None = Field(default=None, ge=1)
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
CountryStatus = Literal["claimed", "likely", "not_found", "mismatch"]


class SupplierQualification(BaseModel):
    result_index: int = Field(..., ge=0, le=4)
    company_name: str = Field(..., min_length=1, max_length=255)
    title_ru: str = Field(..., min_length=1, max_length=500)
    summary_ru: str = Field(..., min_length=1, max_length=1200)
    supplier_type: SupplierKind
    cas_status: CasStatus
    country_status: CountryStatus
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
                    "country_status": {
                        "type": "string",
                        "enum": ["claimed", "likely", "not_found", "mismatch"],
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
                    "country_status",
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

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _search_planner_prompt(prompt: PromptTemplate) -> str:
    return (
        prompt.system_prompt
        + "\nВерни ровно одну строку поискового запроса без объяснений. "
        "Даже если пользователь ввёл название по-русски, используй CAS, "
        "английское название вещества и термины страны поиска."
    )


def _qualification_system_prompt(prompt: PromptTemplate | None) -> str:
    base_prompt = (
        prompt.system_prompt
        if prompt
        else "Оцени поставщиков химического сырья только по переданным свидетельствам."
    )
    return (
        base_prompt
        + "\n\nОтветь на русском языке. Для каждого результата верни ровно одну "
        "оценку с тем же result_index. Не считай текст сайта независимым "
        "подтверждением: GMP, ISO, CoA и TDS могут иметь статус claimed только "
        "при явном упоминании, иначе not_found. Статус manufacturer допустим "
        "только при прямом заявлении о собственном производстве или заводе. "
        "Для country_status используй claimed при прямом указании нахождения "
        "компании в требуемой стране, likely — только по косвенным признакам "
        "вроде домена или региона, mismatch — при явном указании другой страны, "
        "иначе not_found. Кратко перечисли риски и недостающие доказательства. "
        "Не изменяй CAS, названия компаний и факты источника."
    )


def _can_see_run(user: User, search_run: SearchRun) -> bool:
    return user.role in _SEE_ALL_ROLES or search_run.owner_id == user.id


def _next_agent_sequence(db: Session, search_run_id: int) -> int:
    latest = db.scalar(
        select(func.max(AgentRun.sequence)).where(
            AgentRun.search_run_id == search_run_id
        )
    )
    return (latest or 0) + 1


def _fallback_query(data: SupplierSearchRequest) -> str:
    country = f" {data.country}" if data.country else ""
    return f'"{data.name}" "{data.cas}" manufacturer supplier{country} CoA'


def _is_china(country: str | None) -> bool:
    return (country or "").strip().casefold() in {
        "china",
        "китай",
        "cn",
        "prc",
        "中国",
    }


def _search_queries(
    data: SupplierSearchRequest, ai_query: str | None
) -> list[str]:
    """Строит независимые запросы: ИИ, общий и локализованные по стране."""
    candidates = [ai_query, _fallback_query(data)]
    if _is_china(data.country):
        candidates.extend(
            [
                f'"{data.name}" "{data.cas}" (manufacturer OR factory) China',
                f'"{data.cas}" (生产厂家 OR 工厂) 中国',
                f'"{data.cas}" (manufacturer OR factory) site:.cn',
            ]
        )
    elif data.country:
        candidates.append(
            f'"{data.name}" "{data.cas}" manufacturer factory "{data.country}"'
        )

    unique: list[str] = []
    for query in candidates:
        if query and query not in unique:
            unique.append(query)
    return unique


def _domain_key(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname.removeprefix("www.") or url


def _country_score(result: dict, country: str | None) -> int:
    """Эвристика только для ранжирования; не считается подтверждением страны."""
    if not country:
        return 0
    domain = _domain_key(result["url"])
    text = f'{result.get("title", "")} {result.get("snippet", "")}'.casefold()
    if _is_china(country):
        score = 3 if domain.endswith(".cn") else 0
        if any(marker in text for marker in ("china", "chinese", "中国")):
            score += 2
        if any(
            place in text
            for place in (
                "shanghai",
                "shandong",
                "jiangsu",
                "zhejiang",
                "hubei",
                "hebei",
                "guangdong",
                "anhui",
                "henan",
                "sichuan",
                "beijing",
                "tianjin",
                "ningbo",
                "suzhou",
                "wuhan",
                "qingdao",
            )
        ):
            score += 1
        return score
    return 2 if country.casefold() in text else 0


def _country_hint(score: int) -> str:
    if score >= 2:
        return "likely"
    if score == 1:
        return "possible"
    return "unknown"


def _rank_results(
    results: list[dict], country: str | None, limit: int
) -> list[dict]:
    """Оставляет лучшую страницу домена и поднимает признаки нужной страны."""
    best_by_domain: dict[str, tuple[int, int, dict]] = {}
    for position, result in enumerate(results):
        score = _country_score(result, country)
        key = _domain_key(result["url"])
        previous = best_by_domain.get(key)
        if previous is None or score > previous[0]:
            best_by_domain[key] = (score, position, result)
    ranked = sorted(best_by_domain.values(), key=lambda item: (-item[0], item[1]))
    return [
        {**result, "country_hint": _country_hint(score)}
        for score, _, result in ranked[:limit]
    ]


@router.post("")
def supplier_search(
    data: SupplierSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    search_run = create_search_run(
        db,
        owner_id=user.id,
        input_payload=data.model_dump(),
    )
    db.commit()

    prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "supplier_search",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    ai_query: str | None = None
    ai_used = False
    planner_error: str | None = None
    llm = LLMClient()
    if prompt:
        base_system_prompt = _search_planner_prompt(prompt)
        planner_input = {
            "name": data.name,
            "cas": data.cas,
            "country": data.country or "любая",
            "additional_instructions": data.additional_instructions,
        }
        effective_prompt = llm.effective_text_system_prompt(
            base_system_prompt, data.additional_instructions
        )
        planner_run, planner_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=1,
            agent_slug="search_planner",
            agent_name="Планировщик поиска",
            execution_type="llm",
            input_payload=planner_input,
            prompt=prompt,
            effective_system_prompt=effective_prompt,
            model=llm.model,
            temperature=0.1,
            max_tokens=64,
        )
        db.commit()
        try:
            generated = llm.generate_text(
                system_prompt=base_system_prompt,
                user_text=(
                    f"Вещество: {data.name}\nCAS: {data.cas}\n"
                    f"Страна: {data.country or 'любая'}"
                ),
                additional_instructions=data.additional_instructions,
                # A search query is one short line; a larger budget only adds latency.
                max_tokens=64,
            )
            candidate = generated.strip().strip("`").splitlines()[0].strip()
            if 5 <= len(candidate) <= 500:
                ai_query = candidate
                ai_used = True
            finish_agent_run(
                planner_run,
                planner_clock,
                output_payload={
                    "raw_text": generated,
                    "ai_query": ai_query,
                    "accepted": ai_used,
                },
            )
        except LLMUnavailableError as exc:
            planner_error = str(exc)
            finish_agent_run(planner_run, planner_clock, error=planner_error)
        db.commit()
    else:
        planner_run, planner_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=1,
            agent_slug="search_planner",
            agent_name="Планировщик поиска",
            execution_type="deterministic",
            input_payload=data.model_dump(),
        )
        finish_agent_run(
            planner_run,
            planner_clock,
            output_payload={
                "ai_query": None,
                "accepted": False,
                "fallback_reason": "Активный промпт поиска не найден",
            },
        )
        db.commit()

    planned_queries = _search_queries(data, ai_query)
    search_stage, search_clock = start_agent_run(
        db,
        search_run=search_run,
        sequence=2,
        agent_slug="web_search",
        agent_name="Поиск в открытых источниках",
        execution_type="tool",
        input_payload={
            "queries": planned_queries,
            "limit": data.limit,
            "country": data.country,
        },
    )
    db.commit()

    attempted_queries: list[str] = []
    raw_results: list[dict] = []
    search_errors: list[str] = []
    fetch_limit = min(data.limit * 2, 20)
    for query in planned_queries:
        attempted_queries.append(query)
        attempt, attempt_clock = start_search_attempt(
            db,
            search_run=search_run,
            agent_run=search_stage,
            connector="duckduckgo_html",
            query=query,
            language="zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en",
            source_type="search_results",
            purpose="Найти страницы кандидатов-поставщиков",
        )
        db.commit()
        try:
            query_results = search_web(query, fetch_limit)
            raw_results.extend(query_results)
            finish_search_attempt(
                attempt,
                attempt_clock,
                result_count=len(query_results),
                results_payload=query_results,
            )
        except Exception as exc:
            error = str(exc)
            search_errors.append(error)
            finish_search_attempt(attempt, attempt_clock, error=error)
            db.commit()
            continue
        db.commit()
        current = _rank_results(raw_results, data.country, data.limit)
        country_candidates = sum(
            item["country_hint"] == "likely" for item in current
        )
        if len(current) >= data.limit and country_candidates >= data.limit:
            break

    if not raw_results and search_errors:
        error = f"Поисковый источник недоступен: {search_errors[-1]}"
        finish_agent_run(search_stage, search_clock, error=error)
        finish_search_run(search_run, error=error)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={"message": error, "search_run_id": search_run.id},
        )
    results = _rank_results(raw_results, data.country, data.limit)
    fallback_used = len(attempted_queries) > 1
    finish_agent_run(
        search_stage,
        search_clock,
        output_payload={
            "queries_used": attempted_queries,
            "results": results,
            "errors": search_errors,
            "planner_fallback_reason": planner_error,
        },
    )
    search_run.status = "search_completed"
    db.commit()
    return {
        "search_run_id": search_run.id,
        "query": attempted_queries[0],
        "queries_used": attempted_queries,
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
    user: User = Depends(get_current_user),
) -> dict:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    search_run = (
        db.get(SearchRun, data.search_run_id)
        if data.search_run_id is not None
        else None
    )
    if data.search_run_id is not None and (
        search_run is None or not _can_see_run(user, search_run)
    ):
        raise HTTPException(status_code=404, detail="Search run not found")
    if search_run is None:
        search_run = create_search_run(
            db,
            owner_id=user.id,
            input_payload={
                "cas": data.cas,
                "name": data.name,
                "country": data.country,
                "additional_instructions": data.additional_instructions,
            },
        )
        db.commit()

    prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "qualification",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    # Keep this assignment close to the call: the same effective prompt is
    # persisted below and shown verbatim in the search trace.
    system_prompt = _qualification_system_prompt(prompt)
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
    llm = LLMClient()
    qualification_run, qualification_clock = start_agent_run(
        db,
        search_run=search_run,
        sequence=_next_agent_sequence(db, search_run.id),
        agent_slug="supplier_qualification",
        agent_name="Квалификация поставщиков",
        execution_type="llm",
        input_payload=source_data,
        prompt=prompt,
        effective_system_prompt=llm.effective_json_system_prompt(system_prompt),
        model=llm.model,
        temperature=0,
        max_tokens=768,
    )
    db.commit()
    try:
        raw = llm.generate_json(
            system_prompt=system_prompt,
            user_text=json.dumps(source_data, ensure_ascii=False),
            schema_name="supplier_qualification",
            json_schema=_QUALIFICATION_SCHEMA,
            max_tokens=768,
        )
    except LLMUnavailableError as exc:
        error = f"Qwen недоступна: {exc}"
        finish_agent_run(
            qualification_run,
            qualification_clock,
            error=error,
        )
        finish_search_run(search_run, error=error)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"message": error, "search_run_id": search_run.id},
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
                    "country_status": "not_found",
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

    finish_agent_run(
        qualification_run,
        qualification_clock,
        output_payload={
            "model_output": raw,
            "qualified_results": combined_results,
        },
    )
    finish_search_run(search_run)
    db.commit()
    return {
        "search_run_id": search_run.id,
        "results": combined_results,
        "prompt_id": prompt.id if prompt else None,
        "prompt_version": prompt.version if prompt else None,
        "warning": (
            "Квалификация предварительная и основана только на фрагментах "
            "поисковой выдачи. Сертификаты и статус производителя требуют "
            "проверки по первичным документам."
        ),
    }
