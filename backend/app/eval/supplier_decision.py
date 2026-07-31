"""Versioned safety eval for the supplier decision layer.

The eval replays persisted-style artifacts (qualified result, verified
evidence claims, raw auditor response) through the same typed contract and
deterministic veto gate that production uses. It needs no network and no LLM,
so it can run in CI as a release gate: a new false accept on a safety-critical
case must fail the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.supplier_verification import SupplierVerification
from app.services.supplier_verification import apply_supplier_verification

DATASET_DIR = Path(__file__).resolve().parent / "datasets"

_REQUIRED_CATEGORIES = {
    "true_positive",
    "substance_negative",
    "role_negative",
    "process_negative",
}


class SupplierEvalError(ValueError):
    """Raised when the eval dataset is missing or structurally invalid."""


def dataset_path(version: str) -> Path:
    return DATASET_DIR / f"supplier_decision_eval.{version}.json"


def load_dataset(version: str = "v1") -> dict[str, Any]:
    path = dataset_path(version)
    if not path.is_file():
        raise SupplierEvalError(
            f"Датасет supplier_decision_eval.{version}.json не найден."
        )
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("dataset_version") != version:
        raise SupplierEvalError(
            "dataset_version внутри файла не совпадает с именем файла."
        )
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SupplierEvalError("Датасет не содержит ни одного примера.")
    seen_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or case_id in seen_ids:
            raise SupplierEvalError(
                "Каждый пример должен иметь уникальный строковый id."
            )
        seen_ids.add(case_id)
        if case.get("category") not in _REQUIRED_CATEGORIES:
            raise SupplierEvalError(
                f"Пример {case_id} имеет неизвестную категорию."
            )
        expected = case.get("expected")
        if (
            not isinstance(expected, dict)
            or expected.get("status") not in {
                "confirmed",
                "needs_review",
                "rejected",
                "unavailable",
            }
            or not isinstance(expected.get("shortlist_eligible"), bool)
        ):
            raise SupplierEvalError(
                f"Пример {case_id} не содержит ожидаемое policy-решение."
            )
    return dataset


def _parse_verification(
    raw_verification: Any,
) -> tuple[SupplierVerification | None, str | None]:
    if not isinstance(raw_verification, dict):
        return None, "Ответ аудитора не является JSON-объектом."
    try:
        return SupplierVerification.model_validate(raw_verification), None
    except ValidationError as exc:
        return None, f"Ответ аудитора не прошёл typed-контракт: {exc}"[:1200]


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one persisted example through the current deterministic gate."""
    parsed, parse_error = _parse_verification(case.get("raw_verification"))
    decided = apply_supplier_verification(
        dict(case.get("qualified_result") or {}),
        parsed,
        list(case.get("evidence_items") or []),
        unavailable_reason=parse_error,
    )
    verification = decided.get("verification") or {}
    actual_status = verification.get("status")
    actual_shortlist = bool(decided.get("shortlist_eligible"))
    expected = case["expected"]
    status_matches = actual_status == expected["status"]
    shortlist_matches = actual_shortlist == expected["shortlist_eligible"]
    false_accept = actual_shortlist and not expected["shortlist_eligible"]
    return {
        "id": case["id"],
        "category": case["category"],
        "safety_critical": bool(case.get("safety_critical")),
        "expected_status": expected["status"],
        "actual_status": actual_status,
        "expected_shortlist_eligible": expected["shortlist_eligible"],
        "actual_shortlist_eligible": actual_shortlist,
        "gate_reason": verification.get("gate_reason"),
        "invalid_claim_ids": verification.get("invalid_claim_ids", []),
        "passed": status_matches and shortlist_matches,
        "false_accept": false_accept,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def run_supplier_decision_eval(version: str = "v1") -> dict[str, Any]:
    """Evaluate every case and aggregate release-gate metrics."""
    dataset = load_dataset(version)
    case_reports = [evaluate_case(case) for case in dataset["cases"]]

    substance_cases = [
        report
        for report in case_reports
        if report["category"] == "substance_negative"
    ]
    role_cases = [
        report for report in case_reports if report["category"] == "role_negative"
    ]
    shortlisted = [
        report for report in case_reports if report["actual_shortlist_eligible"]
    ]
    abstained = [
        report
        for report in case_reports
        if report["actual_status"] in {"needs_review", "unavailable"}
    ]
    safety_violations = [
        report["id"]
        for report in case_reports
        if report["safety_critical"] and report["false_accept"]
    ]
    metrics = {
        "case_count": len(case_reports),
        "passed_count": sum(report["passed"] for report in case_reports),
        "substance_false_accept_rate": _rate(
            sum(report["false_accept"] for report in substance_cases),
            len(substance_cases),
        ),
        "manufacturer_false_accept_rate": _rate(
            sum(report["false_accept"] for report in role_cases),
            len(role_cases),
        ),
        "shortlist_precision": _rate(
            sum(
                report["expected_shortlist_eligible"] for report in shortlisted
            ),
            len(shortlisted),
        ),
        "abstention_rate": _rate(len(abstained), len(case_reports)),
    }
    return {
        "dataset_version": dataset["dataset_version"],
        "dataset_description": dataset.get("description"),
        "scope": "deterministic_decision_layer",
        "metrics": metrics,
        "safety_violations": safety_violations,
        "failed_case_ids": [
            report["id"] for report in case_reports if not report["passed"]
        ],
        "cases": case_reports,
    }
