"""Детерминированная проверка выводов агента о паспорте качества.

Правила те же, что у аудитора поставщиков: модель не может создать факт.
Утверждение принимается, только если его цитата дословно есть в сохранённом
тексте документа. CAS и номер партии дополнительно сверяются кодом, а не
доверием к модели.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.document_verification import DocumentVerification
from app.services.cas import normalize_cas

# Без этих утверждений документ не может быть принят автоматически.
_REQUIRED_CLAIMS = {"chemical_identity", "batch"}
_CAS_PATTERN = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _quote_is_verbatim(quote: str, document_text: str) -> bool:
    """Цитата засчитывается только при дословном совпадении."""
    return _normalize_space(quote) in _normalize_space(document_text)


def document_cas_numbers(document_text: str) -> set[str]:
    """Все синтаксически валидные CAS, встречающиеся в документе."""
    from app.services.cas import is_valid_cas

    found = set()
    for candidate in _CAS_PATTERN.findall(document_text or ""):
        normalized = normalize_cas(candidate)
        if is_valid_cas(normalized):
            found.add(normalized)
    return found


def apply_document_verification(
    *,
    verification: DocumentVerification | None,
    document_text: str | None,
    expected_cas: str | None,
    expected_name: str | None = None,
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
            "reason": reason,
            "gate_reason": "Документ не принят до ручной проверки.",
            "accepted_claims": [],
            "rejected_claims": [],
            "missing_fields": ["Независимая проверка документа"],
            "red_flags": [],
            "cas_in_document": [],
            "expected_cas": expected_cas,
        }

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for claim in verification.claims:
        entry = {
            "claim_type": claim.claim_type,
            "claim_value": claim.claim_value,
            "quote": claim.quote,
        }
        if _quote_is_verbatim(claim.quote, document_text):
            accepted.append({**entry, "quote_verified": True})
        else:
            rejected.append(
                {
                    **entry,
                    "quote_verified": False,
                    "rejection_reason": (
                        "цитата дословно не найдена в тексте документа"
                    ),
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
    confirmed = (
        not cas_conflict
        and cas_matches
        and verification.verification_status == "confirmed"
        and verification.recommended_action == "accept"
        and verification.substance_match == "exact"
        and verification.confidence >= 70
        and _REQUIRED_CLAIMS.issubset(accepted_types)
        and not rejected
    )

    if confirmed:
        status = "confirmed"
        gate_reason = (
            "Вещество и номер партии подтверждены дословными цитатами, "
            "CAS в документе совпадает с запросом."
        )
    elif model_rejected or cas_conflict:
        status = "rejected"
        gate_reason = (
            "CAS в документе не совпадает с запросом."
            if cas_conflict
            else "Агент обнаружил несоответствие документа запросу."
        )
    else:
        status = "needs_review"
        gaps: list[str] = []
        if not cas_matches:
            gaps.append("CAS запроса не найден в документе")
        if verification.substance_match != "exact":
            gaps.append("нет точного соответствия вещества")
        if verification.confidence < 70:
            gaps.append("уверенность агента ниже 70")
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
        "substance_match": verification.substance_match,
        "recommended_action": verification.recommended_action,
        "confidence": verification.confidence,
        "reason": verification.reason,
        "gate_reason": gate_reason,
        "accepted_claims": accepted,
        "rejected_claims": rejected,
        "missing_fields": missing_fields,
        "red_flags": red_flags,
        "cas_in_document": sorted(document_cas),
        "expected_cas": normalized_expected or expected_cas,
        "expected_name": expected_name,
    }
