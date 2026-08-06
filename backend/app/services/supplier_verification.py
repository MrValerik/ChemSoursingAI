"""Deterministic veto gate for the independent supplier auditor."""

from __future__ import annotations

from typing import Any

from app.schemas.supplier_verification import SupplierVerification


_REQUIRED_SHORTLIST_CLAIMS = {"chemical_identity", "manufacturer_role"}
_CRITICAL_CLAIMS = {"chemical_identity", "manufacturer_role"}


def apply_supplier_verification(
    result: dict[str, Any],
    verification: SupplierVerification | None,
    evidence_items: list[dict[str, Any]],
    *,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Apply the auditor as a veto without allowing it to invent evidence."""
    payload = dict(result)
    red_flags = list(result.get("red_flags") or [])
    evidence_by_id = {
        int(item["id"]): item
        for item in evidence_items
        if isinstance(item.get("id"), int)
        and item.get("quote_verified") is True
    }

    if verification is None:
        reason = unavailable_reason or (
            "Проверяющий агент не вернул корректную структурированную оценку."
        )
        payload["verification"] = {
            "status": "unavailable",
            "model_status": None,
            "substance_match": "unknown",
            "supplier_role": "unverified",
            "recommended_action": "manual_review",
            "confidence": 0,
            "reason": reason,
            "gate_reason": (
                "Короткий список заблокирован до независимой проверки."
            ),
            "supporting_claim_ids": [],
            "contradictory_claim_ids": [],
            "invalid_claim_ids": [],
            "missing_evidence": ["Независимая проверка кандидата"],
        }
        payload["shortlist_eligible"] = False
        missing_evidence = list(result.get("missing_evidence") or [])
        _append_unique(missing_evidence, "Независимая проверка кандидата")
        payload["missing_evidence"] = missing_evidence
        _append_unique(
            red_flags,
            "Независимая проверка недоступна; требуется ручная проверка",
        )
        payload["red_flags"] = red_flags
        return payload

    requested_support = list(dict.fromkeys(verification.supporting_claim_ids))
    requested_contradictions = list(
        dict.fromkeys(verification.contradictory_claim_ids)
    )
    duplicate_roles = set(requested_support) & set(requested_contradictions)
    supporting_ids = [
        claim_id
        for claim_id in requested_support
        if claim_id not in duplicate_roles
        and evidence_by_id.get(claim_id, {}).get("support_status") == "supports"
    ]
    contradictory_ids = [
        claim_id
        for claim_id in requested_contradictions
        if claim_id not in duplicate_roles
        and evidence_by_id.get(claim_id, {}).get("support_status")
        == "contradicts"
    ]
    invalid_claim_ids = sorted(
        (
            set(requested_support)
            | set(requested_contradictions)
        )
        - set(supporting_ids)
        - set(contradictory_ids)
    )
    supported_types = {
        evidence_by_id[claim_id]["claim_type"] for claim_id in supporting_ids
    }
    contradicted_types = {
        evidence_by_id[claim_id]["claim_type"] for claim_id in contradictory_ids
    }

    base_eligible = bool(result.get("shortlist_eligible"))
    model_rejected = (
        verification.verification_status == "rejected"
        or verification.recommended_action == "reject"
        or verification.substance_match == "mismatch"
        or "chemical_identity" in contradicted_types
    )
    # Балл аудитора сюда не входит намеренно. Он не привязан ни к чему
    # проверяемому: промпт не объяснял шкалу, и модель ставила ноль во
    # всех 102 оценках, из-за чего короткий список не открылся ни разу.
    # Условия ниже опираются на структурные факты и проверенные цитаты —
    # их можно оспорить, посмотрев на страницу.
    confirmed = (
        base_eligible
        and verification.verification_status == "confirmed"
        and verification.recommended_action == "shortlist"
        and verification.substance_match == "exact"
        and verification.supplier_role == "manufacturer"
        and _REQUIRED_SHORTLIST_CLAIMS.issubset(supported_types)
        and not (_CRITICAL_CLAIMS & contradicted_types)
        and not invalid_claim_ids
    )

    if confirmed:
        status = "confirmed"
        gate_reason = (
            "Вещество и собственное производство независимо подтверждены "
            "проверенными цитатами."
        )
    elif model_rejected:
        status = "rejected"
        gate_reason = (
            "Аудитор обнаружил несовпадение вещества или рекомендовал "
            "отклонить кандидата."
        )
    else:
        status = "needs_review"
        missing_gates: list[str] = []
        if not base_eligible:
            missing_gates.append("не пройден базовый порог доказательств")
        if verification.substance_match != "exact":
            missing_gates.append("нет точного соответствия вещества")
        if verification.supplier_role != "manufacturer":
            missing_gates.append("роль производителя не подтверждена")
        # Решение самого аудитора. Раньше эти два условия блокировали
        # кандидата, но в объяснение не попадали: у Anhui Liwei прошли все
        # структурные проверки, аудитор выбрал needs_review, и закупщик
        # видел «Короткий список заблокирован до ручной проверки» без
        # единого слова о причине.
        if verification.verification_status != "confirmed":
            missing_gates.append(
                f"аудитор не подтвердил кандидата ({verification.verification_status})"
            )
        if verification.recommended_action != "shortlist":
            missing_gates.append(
                f"аудитор рекомендует {verification.recommended_action}"
            )
        if not _REQUIRED_SHORTLIST_CLAIMS.issubset(supported_types):
            missing_gates.append("не выбраны обязательные проверенные claims")
        if invalid_claim_ids:
            missing_gates.append("аудитор сослался на недопустимые claims")
        if _CRITICAL_CLAIMS & contradicted_types:
            contradicted_names = ", ".join(
                sorted(_CRITICAL_CLAIMS & contradicted_types)
            )
            missing_gates.append(f"цитата опровергает: {contradicted_names}")
        gate_reason = (
            "Короткий список заблокирован: " + "; ".join(missing_gates)
            if missing_gates
            else "Короткий список заблокирован до ручной проверки."
        )

    payload["verification"] = {
        "status": status,
        "model_status": verification.verification_status,
        "substance_match": verification.substance_match,
        "supplier_role": verification.supplier_role,
        "recommended_action": verification.recommended_action,
        "confidence": verification.confidence,
        "reason": verification.reason,
        "gate_reason": gate_reason,
        "supporting_claim_ids": supporting_ids,
        "contradictory_claim_ids": contradictory_ids,
        "invalid_claim_ids": invalid_claim_ids,
        "missing_evidence": verification.missing_evidence,
    }
    payload["shortlist_eligible"] = confirmed
    missing_evidence = list(result.get("missing_evidence") or [])
    for item in verification.missing_evidence:
        _append_unique(missing_evidence, item)
    payload["missing_evidence"] = missing_evidence
    if not confirmed:
        _append_unique(red_flags, f"Решение аудитора: {gate_reason}")
    payload["red_flags"] = red_flags
    return payload


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
