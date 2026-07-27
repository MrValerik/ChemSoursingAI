"""Поиск кандидатов-поставщиков с доказательствами из открытых источников."""

import json
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.web_page import fetch_web_page
from app.connectors.web_search import search_web
from app.core.db import get_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import (
    AgentRun,
    EvidenceClaim,
    PromptTemplate,
    SearchRun,
    SourceDocument,
    User,
)
from app.models.enums import UserRole
from app.services.search_trace import (
    create_search_run,
    finish_agent_run,
    finish_search_attempt,
    finish_search_run,
    start_agent_run,
    start_search_attempt,
    utc_now,
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
ClaimType = Literal[
    "chemical_identity",
    "manufacturer_role",
    "country",
    "gmp",
    "iso",
    "coa",
    "tds",
]
ClaimSupport = Literal["supports", "contradicts"]


class QualificationEvidence(BaseModel):
    source_document_id: int = Field(..., ge=1)
    claim_type: ClaimType
    claim_value: str = Field(..., min_length=1, max_length=500)
    support_status: ClaimSupport
    quote: str = Field(..., min_length=5, max_length=500)


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
    evidence: list[QualificationEvidence] = Field(default_factory=list, max_length=10)


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
                    "evidence": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_document_id": {
                                    "type": "integer",
                                    "minimum": 1,
                                },
                                "claim_type": {
                                    "type": "string",
                                    "enum": [
                                        "chemical_identity",
                                        "manufacturer_role",
                                        "country",
                                        "gmp",
                                        "iso",
                                        "coa",
                                        "tds",
                                    ],
                                },
                                "claim_value": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                },
                                "support_status": {
                                    "type": "string",
                                    "enum": ["supports", "contradicts"],
                                },
                                "quote": {
                                    "type": "string",
                                    "minLength": 5,
                                    "maxLength": 500,
                                },
                            },
                            "required": [
                                "source_document_id",
                                "claim_type",
                                "claim_value",
                                "support_status",
                                "quote",
                            ],
                            "additionalProperties": False,
                        },
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
                    "evidence",
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
        "иначе not_found. page_text — текст загруженной первичной страницы. "
        "Если fetch_status равен failed, доступен только поисковый snippet: "
        "считай его слабым свидетельством и снижай уверенность. "
        "В evidence включай только факты из page_text. Для каждого факта укажи "
        "source_document_id и короткую quote, дословно скопированную из page_text. "
        "Не переводи и не исправляй quote. По одному факту создавай одну запись. "
        "Если page_text отсутствует, evidence для этого источника должен быть пуст. "
        "Кратко перечисли риски и недостающие доказательства. "
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


def _evidence_rejection_reason(
    evidence: QualificationEvidence,
    *,
    result_index: int,
    source_documents: dict[int, SourceDocument],
    source_indexes: dict[int, int],
) -> str | None:
    source = source_documents.get(evidence.source_document_id)
    if source is None:
        return "source_document_id не принадлежит этому запуску"
    if source_indexes.get(source.id) != result_index:
        return "источник относится к другому кандидату"
    if source.status != "completed" or not source.text_content:
        return "первичная страница не была успешно загружена"
    if evidence.quote not in source.text_content:
        return "цитата дословно не найдена в сохранённом тексте"
    return None


def _apply_evidence_gates(
    qualification: SupplierQualification,
    evidence_items: list[dict],
) -> dict:
    """Prevent high-confidence labels without a validated atomic source."""
    payload = qualification.model_dump(exclude={"evidence"})
    supported = {
        item["claim_type"]
        for item in evidence_items
        if item["support_status"] == "supports"
    }
    contradicted = {
        item["claim_type"]
        for item in evidence_items
        if item["support_status"] == "contradicts"
    }
    red_flags = list(payload["red_flags"])

    def flag(message: str) -> None:
        if message not in red_flags:
            red_flags.append(message)

    if payload["supplier_type"] == "manufacturer" and "manufacturer_role" not in supported:
        payload["supplier_type"] = "unknown"
        flag("Статус производителя не подтверждён проверенной цитатой")

    if "chemical_identity" in contradicted:
        payload["cas_status"] = "mismatch"
    elif payload["cas_status"] == "confirmed" and "chemical_identity" not in supported:
        payload["cas_status"] = "not_found"
        flag("Совпадение вещества не подтверждено проверенной цитатой")

    if "country" in contradicted:
        payload["country_status"] = "mismatch"
    elif payload["country_status"] == "claimed" and "country" not in supported:
        payload["country_status"] = "not_found"
        flag("Страна не подтверждена проверенной цитатой")

    for field, claim_type, label in (
        ("gmp_status", "gmp", "GMP"),
        ("iso_status", "iso", "ISO"),
        ("coa_status", "coa", "CoA"),
        ("tds_status", "tds", "TDS"),
    ):
        if claim_type in contradicted:
            payload[field] = "contradicted"
        elif payload[field] == "claimed" and claim_type not in supported:
            payload[field] = "not_found"
            flag(f"{label} не подтверждён проверенной цитатой")

    payload["red_flags"] = red_flags
    return payload


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
    fetch_run, fetch_clock = start_agent_run(
        db,
        search_run=search_run,
        sequence=_next_agent_sequence(db, search_run.id),
        agent_slug="source_fetch",
        agent_name="Загрузка первичных страниц",
        execution_type="tool",
        input_payload={
            "urls": [result.url for result in data.results],
            "max_pages": len(data.results),
        },
    )
    db.commit()

    fetched_sources: list[dict] = []
    fetch_summary: list[dict] = []
    source_documents_by_id: dict[int, SourceDocument] = {}
    source_index_by_id: dict[int, int] = {}
    for index, result in enumerate(data.results):
        source = SourceDocument(
            search_run_id=search_run.id,
            agent_run_id=fetch_run.id,
            url=result.url,
            domain=_domain_key(result.url),
            title=result.title,
            status="running",
            retrieved_at=utc_now(),
        )
        db.add(source)
        db.flush()
        source_documents_by_id[source.id] = source
        source_index_by_id[source.id] = index
        try:
            page = fetch_web_page(result.url)
            source.final_url = page.final_url
            source.domain = page.domain
            source.title = page.title or result.title
            source.content_type = page.content_type
            source.http_status = page.http_status
            source.text_content = page.text
            source.content_hash = page.content_hash
            source.status = "completed"
            fetched_sources.append(
                {
                    "result_index": index,
                    "source_document_id": source.id,
                    "title": result.title[:300],
                    "snippet": result.snippet[:900],
                    "url": result.url,
                    "domain": source.domain,
                    "fetch_status": "completed",
                    "page_text": page.text[:4000],
                }
            )
        except Exception as exc:
            # A single inaccessible supplier page must not destroy the whole
            # run. Its search snippet remains visible as explicitly weak data.
            source.status = "failed"
            source.error = str(exc)
            fetched_sources.append(
                {
                    "result_index": index,
                    "source_document_id": source.id,
                    "title": result.title[:300],
                    "snippet": result.snippet[:900],
                    "url": result.url,
                    "domain": source.domain,
                    "fetch_status": "failed",
                    "page_text": None,
                    "fetch_error": source.error,
                }
            )
        fetch_summary.append(
            {
                "source_document_id": source.id,
                "url": result.url,
                "status": source.status,
                "content_hash": source.content_hash,
                "error": source.error,
            }
        )
        db.commit()

    finish_agent_run(
        fetch_run,
        fetch_clock,
        output_payload={"sources": fetch_summary},
    )
    db.commit()
    source_data = {
        "chemical": {
            "name": data.name,
            "cas": data.cas,
            "country": data.country,
            "user_requirements": data.additional_instructions,
        },
        "sources": fetched_sources,
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
        max_tokens=1536,
    )
    db.commit()
    try:
        raw = llm.generate_json(
            system_prompt=system_prompt,
            user_text=json.dumps(source_data, ensure_ascii=False),
            schema_name="supplier_qualification",
            json_schema=_QUALIFICATION_SCHEMA,
            max_tokens=1536,
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

    validated_evidence: dict[int, list[dict]] = {}
    rejected_evidence: list[dict] = []
    seen_evidence: set[tuple[int, str, str, str]] = set()
    for result_index, qualification in qualifications.items():
        for evidence in qualification.evidence:
            reason = _evidence_rejection_reason(
                evidence,
                result_index=result_index,
                source_documents=source_documents_by_id,
                source_indexes=source_index_by_id,
            )
            dedupe_key = (
                evidence.source_document_id,
                evidence.claim_type,
                evidence.support_status,
                evidence.quote,
            )
            if reason is None and dedupe_key in seen_evidence:
                reason = "дублирующее доказательство"
            if reason is not None:
                rejected_evidence.append(
                    {
                        "result_index": result_index,
                        **evidence.model_dump(),
                        "rejection_reason": reason,
                    }
                )
                continue

            seen_evidence.add(dedupe_key)
            claim = EvidenceClaim(
                search_run_id=search_run.id,
                agent_run_id=qualification_run.id,
                source_document_id=evidence.source_document_id,
                result_index=result_index,
                claim_type=evidence.claim_type,
                claim_value=evidence.claim_value,
                support_status=evidence.support_status,
                quote=evidence.quote,
                quote_verified=True,
            )
            db.add(claim)
            db.flush()
            validated_evidence.setdefault(result_index, []).append(
                {
                    "id": claim.id,
                    **evidence.model_dump(),
                    "quote_verified": True,
                }
            )

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
                    "evidence": [],
                }
            )
            continue
        evidence_items = validated_evidence.get(index, [])
        qualification_payload = _apply_evidence_gates(
            qualification, evidence_items
        )
        combined_results.append(
            {
                **source.model_dump(),
                **qualification_payload,
                "evidence": evidence_items,
            }
        )

    finish_agent_run(
        qualification_run,
        qualification_clock,
        output_payload={
            "model_output": raw,
            "qualified_results": combined_results,
            "validated_evidence_count": sum(
                len(items) for items in validated_evidence.values()
            ),
            "rejected_evidence": rejected_evidence,
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
            "Квалификация предварительная и основана на сохранённых первичных "
            "страницах; для недоступных сайтов используется только слабый "
            "поисковый сниппет. Сертификаты и статус производителя требуют "
            "проверки по первичным документам."
        ),
    }
