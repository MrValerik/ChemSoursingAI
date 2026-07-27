"""Поиск кандидатов-поставщиков с доказательствами из открытых источников."""

import json
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.pubchem import PubChemConnector
from app.connectors.web_page import fetch_web_page
from app.connectors.web_search import search_web
from app.core.db import get_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import (
    AgentRun,
    EvidenceClaim,
    PromptTemplate,
    RFQ,
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
from app.services.cas import is_valid_cas, normalize_cas
from app.services.supplier_scoring import score_supplier

router = APIRouter(prefix="/supplier-search", tags=["supplier-search"])


class SupplierSearchRequest(BaseModel):
    cas: str = Field(..., min_length=3, max_length=20)
    name: str = Field(..., min_length=2, max_length=255)
    country: str | None = Field(default="China", max_length=100)
    additional_instructions: str | None = Field(default=None, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


class SubstanceIdentity(BaseModel):
    status: Literal["verified", "unverified", "conflict", "invalid_cas"]
    canonical_name: str | None = Field(default=None, max_length=500)
    search_names: list[str] = Field(default_factory=list, max_length=8)
    input_name_matches: bool | None = None
    substance_type: Literal[
        "single_substance", "mixture", "trade_name", "unknown"
    ] = "unknown"
    ambiguities: list[str] = Field(default_factory=list, max_length=5)


class SearchPlanItem(BaseModel):
    query: str = Field(..., min_length=5, max_length=500)
    language: Literal["en", "zh", "ru", "other"]
    purpose: Literal["manufacturer", "product", "documents", "registry"]
    source_type: Literal["official_site", "catalog", "registry", "web"]
    priority: int = Field(..., ge=1, le=5)


class SearchPlan(BaseModel):
    queries: list[SearchPlanItem] = Field(..., min_length=1, max_length=8)


class SupplierSearchJobRead(BaseModel):
    search_run_id: int
    status: Literal["queued"]
    queue_position: int


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


_IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "canonical_name": {"type": ["string", "null"], "maxLength": 500},
        "search_names": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "input_name_matches": {"type": ["boolean", "null"]},
        "substance_type": {
            "type": "string",
            "enum": ["single_substance", "mixture", "trade_name", "unknown"],
        },
        "ambiguities": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 500},
        },
    },
    "required": [
        "canonical_name",
        "search_names",
        "input_name_matches",
        "substance_type",
        "ambiguities",
    ],
    "additionalProperties": False,
}

_SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 5,
                        "maxLength": 500,
                    },
                    "language": {
                        "type": "string",
                        "enum": ["en", "zh", "ru", "other"],
                    },
                    "purpose": {
                        "type": "string",
                        "enum": [
                            "manufacturer",
                            "product",
                            "documents",
                            "registry",
                        ],
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["official_site", "catalog", "registry", "web"],
                    },
                    "priority": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": [
                    "query",
                    "language",
                    "purpose",
                    "source_type",
                    "priority",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


def _identity_system_prompt(prompt: PromptTemplate) -> str:
    return (
        prompt.system_prompt
        + "\n\nРаботай только с переданными фактами PubChem. canonical_name и "
        "каждый элемент search_names должны дословно совпадать с input_name, "
        "iupac_name или одним из synonyms. Не придумывай переводы, синонимы, "
        "формулы и свойства. ambiguities описывай кратко по-русски."
    )


def _search_planner_prompt(prompt: PromptTemplate) -> str:
    return (
        prompt.system_prompt
        + "\n\nСоставь до восьми независимых поисковых запросов. Каждый запрос "
        "обязан содержать CAS дословно. Используй только названия из "
        "переданного identity, не придумывай компании и URL. Покрой поиск "
        "производителя, продукта и документов; для Китая добавь китайский "
        "запрос. Верни только объект по JSON-схеме."
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


def _fallback_identity(
    data: SupplierSearchRequest, lookup: dict
) -> SubstanceIdentity:
    """Build a safe identity without allowing the model to invent aliases."""
    names = [
        lookup.get("iupac_name"),
        data.name,
        *(lookup.get("synonyms") or []),
    ]
    unique_names: list[str] = []
    for name in names:
        if (
            isinstance(name, str)
            and name.strip()
            and name.casefold() not in {item.casefold() for item in unique_names}
        ):
            unique_names.append(name.strip())
        if len(unique_names) == 8:
            break
    all_known_names = {
        name.casefold()
        for name in [lookup.get("iupac_name"), *(lookup.get("synonyms") or [])]
        if isinstance(name, str) and name.strip()
    }
    input_name_matches = (
        data.name.casefold() in all_known_names if lookup.get("found") else None
    )
    return SubstanceIdentity(
        status=(
            "verified"
            if lookup.get("found") and input_name_matches
            else "unverified"
        ),
        canonical_name=lookup.get("iupac_name") or data.name,
        search_names=unique_names or [data.name],
        input_name_matches=input_name_matches,
        substance_type="single_substance" if lookup.get("found") else "unknown",
        ambiguities=(
            []
            if lookup.get("found")
            else [f"PubChem не подтвердил CAS: {lookup.get('error') or 'not_found'}"]
        ),
    )


def _validated_identity(
    data: SupplierSearchRequest, lookup: dict, raw: dict
) -> SubstanceIdentity:
    """Accept only names that were present in the immutable lookup payload."""
    fallback = _fallback_identity(data, lookup)
    allowed_values = [
        data.name,
        lookup.get("iupac_name"),
        *(lookup.get("synonyms") or []),
    ]
    allowed = {
        value.casefold(): value.strip()
        for value in allowed_values
        if isinstance(value, str) and value.strip()
    }
    parsed = SubstanceIdentity(
        status="verified" if lookup.get("found") else "unverified",
        **raw,
    )
    canonical = (
        allowed.get(parsed.canonical_name.casefold())
        if parsed.canonical_name
        else None
    )
    search_names: list[str] = []
    for name in parsed.search_names:
        accepted = allowed.get(name.casefold())
        if accepted and accepted.casefold() not in {
            item.casefold() for item in search_names
        }:
            search_names.append(accepted)
    if not search_names:
        return fallback
    return parsed.model_copy(
        update={
            "status": (
                "conflict"
                if parsed.input_name_matches is False
                else "verified"
                if parsed.input_name_matches is True
                else "unverified"
            ),
            "canonical_name": canonical or fallback.canonical_name,
            "search_names": search_names,
        }
    )


def _fallback_search_plan(
    data: SupplierSearchRequest, identity: SubstanceIdentity
) -> list[SearchPlanItem]:
    """Build deterministic coverage that remains available without the LLM."""
    preferred_name = identity.canonical_name or data.name
    candidates = [
        SearchPlanItem(
            query=_fallback_query(
                data.model_copy(update={"name": preferred_name})
            ),
            language="en",
            purpose="manufacturer",
            source_type="web",
            priority=1,
        )
    ]
    if _is_china(data.country):
        candidates.extend(
            [
                SearchPlanItem(
                    query=f'"{preferred_name}" "{data.cas}" (manufacturer OR factory) China',
                    language="en",
                    purpose="manufacturer",
                    source_type="official_site",
                    priority=2,
                ),
                SearchPlanItem(
                    query=f'"{data.cas}" (生产厂家 OR 工厂) 中国',
                    language="zh",
                    purpose="manufacturer",
                    source_type="official_site",
                    priority=2,
                ),
                SearchPlanItem(
                    query=f'"{data.cas}" (CoA OR TDS OR SDS) site:.cn',
                    language="en",
                    purpose="documents",
                    source_type="official_site",
                    priority=3,
                ),
            ]
        )
    elif data.country:
        candidates.append(
            SearchPlanItem(
                query=f'"{preferred_name}" "{data.cas}" manufacturer factory "{data.country}"',
                language="en",
                purpose="manufacturer",
                source_type="official_site",
                priority=2,
            )
        )
    return candidates


def _merge_search_plans(
    data: SupplierSearchRequest,
    ai_items: list[SearchPlanItem],
    fallback_items: list[SearchPlanItem],
) -> tuple[list[SearchPlanItem], int]:
    """Reject unsafe model queries and add deterministic coverage."""
    accepted: list[SearchPlanItem] = []
    seen: set[str] = set()
    rejected_count = 0
    for item in [*ai_items, *fallback_items]:
        normalized = item.query.strip()
        if data.cas not in normalized:
            rejected_count += 1
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        accepted.append(item.model_copy(update={"query": normalized}))
        if len(accepted) == 8:
            break
    return accepted, rejected_count


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


@router.post("/jobs", response_model=SupplierSearchJobRead, status_code=202)
def enqueue_supplier_search(
    data: SupplierSearchRequest,
    rfq_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierSearchJobRead:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    if rfq_id is not None:
        rfq = db.get(RFQ, rfq_id)
        can_see_all = user.role in {
            UserRole.HEAD,
            UserRole.ADMIN,
            UserRole.AUDITOR,
        }
        if rfq is None or (
            not can_see_all
            and rfq.owner_id is not None
            and rfq.owner_id != user.id
        ):
            raise HTTPException(status_code=404, detail="RFQ not found")
        data = data.model_copy(update={"cas": rfq.cas, "name": rfq.name})
    normalized_cas = normalize_cas(data.cas)
    if not is_valid_cas(normalized_cas):
        raise HTTPException(
            status_code=422,
            detail="CAS не прошёл проверку формата и контрольной суммы",
        )
    payload = data.model_copy(update={"cas": normalized_cas}).model_dump()
    search_run = create_search_run(
        db,
        owner_id=user.id,
        rfq_id=rfq_id,
        input_payload=payload,
        mode="queued_search",
        status="queued",
    )
    db.commit()
    queue_position = db.scalar(
        select(func.count(SearchRun.id)).where(
            SearchRun.mode == "queued_search",
            SearchRun.status == "queued",
            SearchRun.id <= search_run.id,
        )
    )
    return SupplierSearchJobRead(
        search_run_id=search_run.id,
        status="queued",
        queue_position=queue_position or 1,
    )


@router.post("")
def supplier_search(
    data: SupplierSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return execute_supplier_search(data, db, user)


def execute_supplier_search(
    data: SupplierSearchRequest,
    db: Session,
    user: User,
    *,
    search_run: SearchRun | None = None,
) -> dict:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    if search_run is None:
        search_run = create_search_run(
            db,
            owner_id=user.id,
            input_payload=data.model_dump(),
        )
    elif search_run.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Чужая задача поиска")
    search_run.status = "identifying"
    search_run.error = None
    search_run.completed_at = None
    db.commit()

    normalized_cas = normalize_cas(data.cas)
    lookup_stage, lookup_clock = start_agent_run(
        db,
        search_run=search_run,
        sequence=1,
        agent_slug="substance_lookup",
        agent_name="Проверка CAS в PubChem",
        execution_type="tool",
        input_payload={"cas": normalized_cas},
    )
    db.commit()
    lookup = PubChemConnector().verify_cas(normalized_cas).as_dict()
    finish_agent_run(lookup_stage, lookup_clock, output_payload=lookup)
    db.commit()
    if not is_valid_cas(normalized_cas):
        error = "CAS не прошёл проверку формата и контрольной суммы"
        finish_search_run(search_run, error=error)
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": error, "search_run_id": search_run.id},
        )
    data = data.model_copy(update={"cas": normalized_cas})

    identity_prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "substance_identity",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    llm = LLMClient()
    identity = _fallback_identity(data, lookup)
    identity_error: str | None = None
    identity_input = {
        "input_name": data.name,
        "cas": normalized_cas,
        "pubchem": lookup,
    }
    if identity_prompt and lookup.get("found"):
        identity_system_prompt = _identity_system_prompt(identity_prompt)
        identity_run, identity_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=2,
            agent_slug="substance_identity",
            agent_name="Агент идентичности вещества",
            execution_type="llm",
            input_payload=identity_input,
            prompt=identity_prompt,
            effective_system_prompt=llm.effective_json_system_prompt(
                identity_system_prompt
            ),
            model=llm.model,
            temperature=0,
            max_tokens=512,
        )
        db.commit()
        try:
            raw_identity = llm.generate_json(
                system_prompt=identity_system_prompt,
                user_text=json.dumps(identity_input, ensure_ascii=False),
                schema_name="substance_identity",
                json_schema=_IDENTITY_SCHEMA,
                max_tokens=512,
            )
            identity = _validated_identity(data, lookup, raw_identity)
            finish_agent_run(
                identity_run,
                identity_clock,
                output_payload={
                    "identity": identity.model_dump(),
                    "raw": raw_identity,
                },
            )
        except (LLMUnavailableError, ValidationError) as exc:
            identity_error = str(exc)
            finish_agent_run(
                identity_run,
                identity_clock,
                output_payload={
                    "identity": identity.model_dump(),
                    "fallback_reason": identity_error,
                },
            )
        db.commit()
    else:
        identity_run, identity_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=2,
            agent_slug="substance_identity",
            agent_name="Агент идентичности вещества",
            execution_type="deterministic",
            input_payload=identity_input,
        )
        identity_error = (
            "Активный промпт идентичности не найден"
            if not identity_prompt
            else f"PubChem недоступен: {lookup.get('error') or 'not_found'}"
        )
        finish_agent_run(
            identity_run,
            identity_clock,
            output_payload={
                "identity": identity.model_dump(),
                "fallback_reason": identity_error,
            },
        )
        db.commit()

    search_run.status = "planning"
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
    ai_items: list[SearchPlanItem] = []
    ai_used = False
    planner_error: str | None = None
    if prompt:
        base_system_prompt = _search_planner_prompt(prompt)
        planner_input = {
            "identity": identity.model_dump(),
            "cas": normalized_cas,
            "country": data.country or "любая",
            "additional_instructions": data.additional_instructions,
        }
        planner_run, planner_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=3,
            agent_slug="search_planner",
            agent_name="Планировщик поиска",
            execution_type="llm",
            input_payload=planner_input,
            prompt=prompt,
            effective_system_prompt=llm.effective_json_system_prompt(
                base_system_prompt
            ),
            model=llm.model,
            temperature=0,
            max_tokens=1024,
        )
        db.commit()
        try:
            generated = llm.generate_json(
                system_prompt=base_system_prompt,
                user_text=json.dumps(planner_input, ensure_ascii=False),
                schema_name="supplier_search_plan",
                json_schema=_SEARCH_PLAN_SCHEMA,
                max_tokens=1024,
            )
            parsed_plan = SearchPlan.model_validate(generated)
            ai_items = parsed_plan.queries
            ai_used = bool(ai_items)
            finish_agent_run(
                planner_run,
                planner_clock,
                output_payload={
                    "raw": generated,
                    "queries": [item.model_dump() for item in ai_items],
                    "accepted": ai_used,
                },
            )
        except (LLMUnavailableError, ValidationError) as exc:
            planner_error = str(exc)
            finish_agent_run(
                planner_run,
                planner_clock,
                output_payload={
                    "queries": [],
                    "accepted": False,
                    "fallback_reason": planner_error,
                },
            )
        db.commit()
    else:
        planner_run, planner_clock = start_agent_run(
            db,
            search_run=search_run,
            sequence=3,
            agent_slug="search_planner",
            agent_name="Планировщик поиска",
            execution_type="deterministic",
            input_payload=data.model_dump(),
        )
        finish_agent_run(
            planner_run,
            planner_clock,
            output_payload={
                "queries": [],
                "accepted": False,
                "fallback_reason": "Активный промпт поиска не найден",
            },
        )
        db.commit()

    fallback_items = _fallback_search_plan(data, identity)
    planned_queries, rejected_queries = _merge_search_plans(
        data, ai_items, fallback_items
    )
    search_run.status = "searching"
    db.commit()
    search_stage, search_clock = start_agent_run(
        db,
        search_run=search_run,
        sequence=4,
        agent_slug="web_search",
        agent_name="Поиск в открытых источниках",
        execution_type="tool",
        input_payload={
            "queries": [item.model_dump() for item in planned_queries],
            "limit": data.limit,
            "country": data.country,
        },
    )
    db.commit()

    attempted_queries: list[str] = []
    raw_results: list[dict] = []
    search_errors: list[str] = []
    fetch_limit = min(data.limit * 2, 20)
    for plan_item in planned_queries:
        query = plan_item.query
        attempted_queries.append(query)
        attempt, attempt_clock = start_search_attempt(
            db,
            search_run=search_run,
            agent_run=search_stage,
            connector="duckduckgo_html",
            query=query,
            language=plan_item.language,
            source_type=plan_item.source_type,
            purpose=plan_item.purpose,
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
            "search_plan": [item.model_dump() for item in planned_queries],
            "results": results,
            "errors": search_errors,
            "planner_fallback_reason": planner_error,
            "identity_fallback_reason": identity_error,
            "rejected_model_queries": rejected_queries,
        },
    )
    response_payload = {
        "search_run_id": search_run.id,
        "query": attempted_queries[0],
        "queries_used": attempted_queries,
        "identity": identity.model_dump(),
        "substance_lookup": lookup,
        "search_plan": [item.model_dump() for item in planned_queries],
        "ai_query": ai_items[0].query if ai_items else None,
        "ai_used": ai_used,
        "fallback_used": fallback_used or bool(identity_error) or rejected_queries > 0,
        "results": results,
        "warning": (
            "Результаты являются кандидатами. Статус производителя и документы "
            "необходимо подтвердить по первичному источнику."
        ),
    }
    search_run.result_payload = response_payload
    search_run.status = "search_completed"
    db.commit()
    return response_payload


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
    search_run.status = "fetching_sources"
    db.commit()
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
    search_run.status = "qualifying"
    db.commit()
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
                    "llm_confidence": None,
                    "score_breakdown": score_supplier(
                        {"supplier_type": "unknown", "cas_status": "not_found"},
                        [],
                    ).to_dict(),
                    "shortlist_eligible": False,
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
        score = score_supplier(qualification_payload, evidence_items)
        qualification_payload["llm_confidence"] = qualification.confidence
        qualification_payload["confidence"] = score.total
        qualification_payload["score_breakdown"] = score.to_dict()
        qualification_payload["shortlist_eligible"] = score.shortlist_eligible
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
