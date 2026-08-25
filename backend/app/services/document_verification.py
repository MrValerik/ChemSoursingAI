"""Детерминированная проверка выводов агента о паспорте качества.

Правила те же, что у аудитора поставщиков: модель не может создать факт.
Утверждение принимается, только если его цитата дословно есть в сохранённом
тексте документа. CAS и номер партии дополнительно сверяются кодом, а не
доверием к модели.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.schemas.document_verification import DocumentVerification
from app.services.cas import normalize_cas

# Без этих утверждений документ не может быть принят автоматически.
_REQUIRED_CLAIMS = {"chemical_identity", "batch"}
_CAS_PATTERN = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_PRODUCT_NAME_PATTERN = re.compile(
    r"(?im)^\s*(?:product\s+name|chemical\s+name|substance\s+name|"
    r"наименование|название\s+вещества)\s*[:\-]\s*([^\r\n]+)"
)
_BATCH_VALUE_PATTERN = re.compile(
    r"(?i)(?:batch(?:\s+no\.?)?|lot(?:\s+no\.?)?|номер\s+партии)\s*[:#\-]\s*([^\r\n;]+)"
)
_DOCUMENT_KIND_MARKERS = {
    "coa": ("certificate of analysis", "паспорт качества", "сертификат анализа"),
    "tds": ("technical data sheet", "technical datasheet", "техническая спецификация"),
    "msds": ("material safety data sheet", "safety data sheet", "паспорт безопасности"),
}
_SUPPORTING_CLAIMS = {
    "manufacture_date",
    "expiry_date",
    "standard",
    "assay",
    "impurity",
    "manufacturer",
    "conclusion",
}
_CYRILLIC_TRANSLITERATION = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalize_name(value: str) -> str:
    """Сопоставимое представление названия, включая русскую транслитерацию."""
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    transliterated = decomposed.translate(_CYRILLIC_TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", " ", transliterated).strip()


def _quote_is_verbatim(quote: str, document_text: str) -> bool:
    """Цитата засчитывается только при дословном совпадении."""
    return _normalize_space(quote) in _normalize_space(document_text)


def _batch_claim_matches_quote(claim_value: str, quote: str) -> bool:
    """Номер партии в structured value должен совпасть с самой цитатой."""
    match = _BATCH_VALUE_PATTERN.search(quote or "")
    if match is None:
        return False
    stated = _normalize_name(match.group(1))
    claimed = _normalize_name(claim_value)
    return bool(stated and claimed and (stated in claimed or claimed in stated))


def document_cas_numbers(document_text: str) -> set[str]:
    """Все синтаксически валидные CAS, встречающиеся в документе."""
    from app.services.cas import is_valid_cas

    found = set()
    for candidate in _CAS_PATTERN.findall(document_text or ""):
        normalized = normalize_cas(candidate)
        if is_valid_cas(normalized):
            found.add(normalized)
    return found


def document_product_names(document_text: str) -> list[str]:
    """Названия только из явно подписанных полей документа."""
    return [match.strip() for match in _PRODUCT_NAME_PATTERN.findall(document_text or "")]


def document_kind_from_text(document_text: str) -> str:
    """Распознаёт стандартный тип документа по его заголовку без модели."""
    normalized = _normalize_space(document_text or "")
    for kind, markers in _DOCUMENT_KIND_MARKERS.items():
        if any(marker in normalized for marker in markers):
            return kind
    return "unknown"


def _name_matches(expected_name: str | None, document_text: str) -> bool:
    expected = _normalize_name(expected_name or "")
    if len(expected) < 3:
        return False
    for candidate in document_product_names(document_text):
        normalized = _normalize_name(candidate)
        if (
            normalized == expected
            or normalized.startswith(expected + " ")
            or normalized.endswith(" " + expected)
        ):
            return True
    return False


def _confidence_breakdown(
    *,
    identity_basis: str,
    accepted_types: set[str],
    accepted_count: int,
    rejected_count: int,
    deterministic_document_kind: str,
    text_status: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    """Считает воспроизводимый балл только по проверяемым признакам."""
    identity_points = {
        "cas": 45,
        "name": 40,
        "name_with_missing_expected_cas": 25,
    }.get(identity_basis, 0)
    identity_reason = {
        "cas": "CAS с корректной контрольной суммой совпал с запросом.",
        "name": "CAS не задан; название совпало с явно подписанным полем документа.",
        "name_with_missing_expected_cas": (
            "Название совпало, но заданный в запросе CAS в документе отсутствует."
        ),
        "conflict": "В документе найден другой валидный CAS.",
    }.get(identity_basis, "Идентичность вещества не подтверждена кодом.")

    batch_points = 20 if "batch" in accepted_types else 0
    total_claims = accepted_count + rejected_count
    citation_points = (
        round(20 * accepted_count / total_claims) if total_claims else 0
    )
    kind_points = 5 if deterministic_document_kind != "unknown" else 0
    supporting_count = len(accepted_types & _SUPPORTING_CLAIMS)
    supporting_points = min(10, supporting_count * 2)

    breakdown = [
        {
            "key": "identity",
            "label": "Идентичность вещества",
            "score": identity_points,
            "max_score": 45,
            "reason": identity_reason,
        },
        {
            "key": "batch",
            "label": "Номер партии",
            "score": batch_points,
            "max_score": 20,
            "reason": (
                "Номер партии подтверждён дословной цитатой."
                if batch_points
                else "Нет подтверждённой цитаты с номером партии."
            ),
        },
        {
            "key": "citations",
            "label": "Проверка цитат",
            "score": citation_points,
            "max_score": 20,
            "reason": (
                f"Дословно найдено {accepted_count} из {total_claims} утверждений."
                if total_claims
                else "Агент не предоставил проверяемых утверждений."
            ),
        },
        {
            "key": "document_structure",
            "label": "Структура и дополнительные поля",
            "score": kind_points + supporting_points,
            "max_score": 15,
            "reason": (
                f"Тип документа распознан; дополнительных подтверждённых полей: "
                f"{supporting_count}."
                if kind_points
                else "Тип документа не распознан как CoA, TDS или MSDS."
            ),
        },
    ]
    raw_score = sum(item["score"] for item in breakdown)
    if text_status == "ocr_extracted":
        adjusted = round(raw_score * 0.85)
        breakdown.append(
            {
                "key": "ocr_quality",
                "label": "Надёжность текстового слоя",
                "score": adjusted - raw_score,
                "max_score": 0,
                "reason": "Текст получен OCR; применено снижение 15% из-за риска ошибок распознавания.",
            }
        )
        raw_score = adjusted
    return max(0, min(100, raw_score)), breakdown


def apply_document_verification(
    *,
    verification: DocumentVerification | None,
    document_text: str | None,
    expected_cas: str | None,
    expected_name: str | None = None,
    text_status: str | None = "extracted",
    synthetic_demo: bool = False,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Применяет veto-gate к выводу агента о документе."""
    if verification is None or not document_text:
        reason = unavailable_reason or (
            "Проверяющий агент не вернул корректную структурированную оценку."
        )
        return {
            "status": "unavailable",
            "model_status": None,
            "document_kind": None,
            "substance_match": "unknown",
            "recommended_action": "manual_review",
            "confidence": 0,
            "model_confidence": None,
            "confidence_breakdown": [],
            "reason": reason,
            "gate_reason": "Документ не принят до ручной проверки.",
            "accepted_claims": [],
            "rejected_claims": [],
            "missing_fields": ["Независимая проверка документа"],
            "red_flags": [],
            "cas_in_document": [],
            "expected_cas": expected_cas,
            "expected_name": expected_name,
            "synthetic_demo": synthetic_demo,
        }

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for claim in verification.claims:
        entry = {
            "claim_type": claim.claim_type,
            "claim_value": claim.claim_value,
            "quote": claim.quote,
        }
        quote_verified = _quote_is_verbatim(claim.quote, document_text)
        value_verified = (
            claim.claim_type != "batch"
            or _batch_claim_matches_quote(claim.claim_value, claim.quote)
        )
        if quote_verified and value_verified:
            accepted.append({**entry, "quote_verified": True})
        else:
            rejection_reason = (
                "номер партии в claim_value не совпадает с цитатой"
                if quote_verified and not value_verified
                else "цитата дословно не найдена в тексте документа"
            )
            rejected.append(
                {
                    **entry,
                    "quote_verified": False,
                    "rejection_reason": rejection_reason,
                }
            )

    accepted_types = {claim["claim_type"] for claim in accepted}

    # CAS сверяем сами: это дешёвая детерминированная проверка, которая не
    # должна зависеть от аккуратности модели.
    document_cas = document_cas_numbers(document_text)
    normalized_expected = normalize_cas(expected_cas or "")
    cas_matches = bool(normalized_expected) and normalized_expected in document_cas
    cas_conflict = (
        bool(normalized_expected)
        and bool(document_cas)
        and not cas_matches
    )
    name_matches = _name_matches(expected_name, document_text)
    if cas_matches:
        identity_basis = "cas"
    elif cas_conflict:
        identity_basis = "conflict"
    elif not normalized_expected and name_matches:
        identity_basis = "name"
    elif normalized_expected and not document_cas and name_matches:
        identity_basis = "name_with_missing_expected_cas"
    else:
        identity_basis = "missing"

    confidence, confidence_breakdown = _confidence_breakdown(
        identity_basis=identity_basis,
        accepted_types=accepted_types,
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        deterministic_document_kind=document_kind_from_text(document_text),
        text_status=text_status,
    )

    red_flags = list(verification.red_flags)
    missing_fields = list(verification.missing_fields)

    def flag(message: str) -> None:
        if message not in red_flags:
            red_flags.append(message)

    if cas_conflict:
        flag(
            "В документе указан другой CAS: "
            + ", ".join(sorted(document_cas))
        )
    if normalized_expected and not document_cas:
        if "CAS в документе" not in missing_fields:
            missing_fields.append("CAS в документе")

    model_rejected = (
        verification.verification_status == "rejected"
        or verification.recommended_action == "reject"
        or verification.substance_match == "mismatch"
    )
    deterministic_identity_match = cas_matches or (
        not normalized_expected and name_matches
    )
    model_accepts = (
        verification.verification_status == "confirmed"
        and verification.recommended_action == "accept"
        and verification.substance_match == "exact"
    )
    confirmed = (
        not cas_conflict
        and deterministic_identity_match
        and (model_accepts or synthetic_demo)
        and confidence >= 80
        and _REQUIRED_CLAIMS.issubset(accepted_types)
        and not rejected
    )

    if confirmed:
        status = "confirmed"
        gate_reason = (
            "Вещество и номер партии подтверждены дословными цитатами, "
            + (
                "CAS в документе совпадает с запросом."
                if cas_matches
                else "название совпадает с явно подписанным полем документа."
            )
        )
    elif (model_rejected and not synthetic_demo) or cas_conflict:
        status = "rejected"
        gate_reason = (
            "CAS в документе не совпадает с запросом."
            if cas_conflict
            else "Агент обнаружил несоответствие документа запросу."
        )
    else:
        status = "needs_review"
        gaps: list[str] = []
        if not deterministic_identity_match:
            gaps.append(
                "CAS запроса не найден в документе"
                if normalized_expected
                else "название запроса не совпало с полем названия в документе"
            )
        if verification.substance_match != "exact" and not synthetic_demo:
            gaps.append("нет точного соответствия вещества")
        if confidence < 80:
            gaps.append(f"проверяемая уверенность ниже 80% ({confidence}%)")
        if not _REQUIRED_CLAIMS.issubset(accepted_types):
            gaps.append("не подтверждены вещество и номер партии")
        if rejected:
            gaps.append("часть цитат не найдена в документе")
        gate_reason = (
            "Требуется ручная проверка: " + "; ".join(gaps)
            if gaps
            else "Требуется ручная проверка документа."
        )

    if rejected:
        flag("Часть утверждений агента не подтверждена цитатами из документа")

    return {
        "status": status,
        "model_status": verification.verification_status,
        "document_kind": verification.document_kind,
        "deterministic_document_kind": document_kind_from_text(document_text),
        "substance_match": verification.substance_match,
        "recommended_action": verification.recommended_action,
        "confidence": confidence,
        "model_confidence": verification.confidence,
        "confidence_breakdown": confidence_breakdown,
        "reason": verification.reason,
        "gate_reason": gate_reason,
        "accepted_claims": accepted,
        "rejected_claims": rejected,
        "missing_fields": missing_fields,
        "red_flags": red_flags,
        "cas_in_document": sorted(document_cas),
        "expected_cas": normalized_expected or expected_cas,
        "expected_name": expected_name,
        "name_matches": name_matches,
        "identity_basis": identity_basis,
        "text_status": text_status,
        "synthetic_demo": synthetic_demo,
    }
