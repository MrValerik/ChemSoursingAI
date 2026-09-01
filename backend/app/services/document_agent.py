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
from app.services.communication_llm import communication_llm_client

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
        "Цитата должна быть одним непрерывным фрагментом: не соединяй строки "
        "многоточием, не исправляй OCR и не пересказывай цитату. Для CAS достаточно "
        "короткой строки с его меткой и номером. "
        "Не выводи соответствие EP/USP/BP из набора анализов или слова STANDARD: "
        "название стандарта должно быть явно написано в документе и цитате. "
        "Если текст документа не относится к запрошенному веществу, укажи "
        "substance_match=mismatch и recommended_action=reject. Поле confidence "
        "означает только уверенность в корректности твоей классификации, а не "
        "вероятность принятия документа: защитная маркировка synthetic/demo сама "
        "по себе не снижает эту уверенность. "
        "Не добавляй claims с вымышленными заглушками missing, unknown или N/A: "
        "отсутствие поля указывай только в missing_fields. В первую очередь "
        "подтверди chemical_identity и batch дословными цитатами, затем остальные "
        "существенные поля; не заполняй весь лимит второстепенными примесями. "
        "Если в документе назван изготовитель — в шапке или строкой "
        "Manufacturer/Изготовитель — обязательно верни claim manufacturer: "
        "по нему сверяется, кто выпустил партию, и он важнее лишних assay. "
        "Issue Date, Analysis Date, Test Date, дата анализа и дата выпуска документа "
        "не являются manufacture_date вещества. Если указана только дата анализа, "
        "не добавляй claim manufacture_date, даже с оговоркой в claim_value. "
        "TDS описывает продукт в целом и не заменяет CoA конкретной партии; "
        "не заявляй подтверждение качества партии по общей спецификации. "
        "Если evaluation_context.synthetic_demo=true, оцени внутреннее "
        "соответствие синтетического файла запросу по тем же "
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


def _supplier_company(document: SupplierDocument) -> str | None:
    """Компания, приславшая документ, — вторая сторона сверки изготовителя."""
    supplier = getattr(document, "supplier", None)
    company = (getattr(supplier, "company", None) or "").strip()
    return company or None


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
    # Demo-вложения тестового диалога не имеют обычного communication_id.
    # Серверная отметка сохраняет маршрут при повторных ручных проверках,
    # но не включает synthetic_demo и не ослабляет проверку документа.
    previous = document.verification or {}
    from_communication = (
        document.communication_id is not None
        or synthetic_demo
        or bool(previous.get("synthetic_demo"))
        or bool(previous.get("communication_document"))
    )
    if not document.text_content:
        result = apply_document_verification(
            verification=None,
            document_text=None,
            expected_cas=expected_cas,
            expected_name=expected_name,
            supplier_company=_supplier_company(document),
            text_status=document.text_status,
            synthetic_demo=synthetic_demo,
            unavailable_reason=(
                "Из документа не извлечён текст: "
                f"{document.extraction_error or document.text_status}"
            ),
        )
        result["communication_document"] = from_communication
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
    client = llm or (
        communication_llm_client() if from_communication else LLMClient()
    )
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
        supplier_company=_supplier_company(document),
        text_status=document.text_status,
        synthetic_demo=synthetic_demo,
        unavailable_reason=error,
    )
    result["prompt_id"] = prompt.id if prompt else None
    result["prompt_version"] = prompt.version if prompt else None
    result["model"] = client.model
    result["communication_document"] = from_communication
    document.verification = result
    return result
