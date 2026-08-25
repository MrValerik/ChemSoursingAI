"""Агент независимой проверки паспорта качества.

Отвечает только за вызов модели по сохранённому тексту документа. Решение
принимает детерминированный gate в :mod:`app.services.document_verification`.
"""

from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.extraction.llm_client import (
    LLMClient,
    LLMOutputTruncatedError,
    LLMUnavailableError,
)
from app.models import PromptTemplate, SupplierDocument
from app.schemas.document_verification import (
    DOCUMENT_VERIFICATION_JSON_SCHEMA,
    DocumentVerification,
)
from app.services.document_verification import apply_document_verification

_MAX_OUTPUT_TOKENS = 1024


def document_verification_prompt(prompt: PromptTemplate | None) -> str:
    base = (
        prompt.system_prompt
        if prompt
        else (
            "Проверь паспорт качества поставщика химического сырья только по "
            "переданному тексту документа."
        )
    )
    return (
        base
        + "\n\nВерни ровно один JSON-объект по схеме. В claims включай только "
        "утверждения, подтверждённые дословной цитатой из document_text. "
        "Если текст документа не относится к запрошенному веществу, укажи "
        "substance_match=mismatch и recommended_action=reject. Поле confidence "
        "означает только уверенность в корректности твоей классификации, а не "
        "вероятность принятия документа: защитная маркировка synthetic/demo сама "
        "по себе не снижает эту уверенность. Если evaluation_context.synthetic_demo=true, "
        "оцени внутреннее соответствие синтетического файла запросу по тем же "
        "цитатам, CAS и партии; ожидаемую маркировку demo не считай несоответствием."
    )


def _document_text_budget() -> int:
    """Сколько символов документа помещается в контекст модели."""
    settings = get_settings()
    overhead_tokens = 1000
    available = (
        settings.llm_context_tokens - _MAX_OUTPUT_TOKENS - overhead_tokens
    )
    return max(600, available) * 2


def verify_document(
    db: Session,
    document: SupplierDocument,
    *,
    expected_cas: str | None,
    expected_name: str | None = None,
    synthetic_demo: bool = False,
    llm: LLMClient | None = None,
) -> dict:
    """Проверяет документ и сохраняет итог veto-gate в записи."""
    if not document.text_content:
        result = apply_document_verification(
            verification=None,
            document_text=None,
            expected_cas=expected_cas,
            expected_name=expected_name,
            text_status=document.text_status,
            synthetic_demo=synthetic_demo,
            unavailable_reason=(
                "Из документа не извлечён текст: "
                f"{document.extraction_error or document.text_status}"
            ),
        )
        document.verification = result
        return result

    prompt = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.kind == "document_verification",
            PromptTemplate.is_active.is_(True),
        )
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    system_prompt = document_verification_prompt(prompt)
    client = llm or LLMClient()
    document_text = document.text_content[: _document_text_budget()]
    payload = {
        "requested_substance": {"name": expected_name, "cas": expected_cas},
        "evaluation_context": {"synthetic_demo": synthetic_demo},
        "document": {
            "filename": document.filename,
            "declared_kind": document.kind,
            "page_count": document.page_count,
            "document_text": document_text,
        },
    }

    parsed: DocumentVerification | None = None
    raw: dict | None = None
    error: str | None = None
    try:
        raw = client.generate_json(
            system_prompt=system_prompt,
            user_text=json.dumps(payload, ensure_ascii=False),
            schema_name="document_verification",
            json_schema=DOCUMENT_VERIFICATION_JSON_SCHEMA,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
        parsed = DocumentVerification.model_validate(raw)
    except (LLMUnavailableError, LLMOutputTruncatedError, ValidationError) as exc:
        error = str(exc)[:500]

    result = apply_document_verification(
        verification=parsed,
        document_text=document_text,
        expected_cas=expected_cas,
        expected_name=expected_name,
        text_status=document.text_status,
        synthetic_demo=synthetic_demo,
        unavailable_reason=error,
    )
    result["prompt_id"] = prompt.id if prompt else None
    result["prompt_version"] = prompt.version if prompt else None
    result["model"] = client.model
    document.verification = result
    return result
