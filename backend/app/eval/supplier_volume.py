"""Offline eval for industrial-volume supplier qualification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.page_facts import assess_supply_volume
from app.services.supplier_scoring import score_supplier

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
_CATEGORIES = {"laboratory_negative", "industrial_positive", "unknown_evidence"}
_STRONG_EVIDENCE = [
    {
        "claim_type": claim_type,
        "support_status": "supports",
        "quote_verified": True,
    }
    for claim_type in (
        "chemical_identity",
        "manufacturer_role",
        "country",
        "iso",
        "gmp",
        "coa",
    )
]


class SupplierVolumeEvalError(ValueError):
    """Raised when the versioned volume dataset is invalid."""


def load_supplier_volume_dataset(version: str = "v1") -> dict[str, Any]:
    path = DATASET_DIR / f"supplier_volume_eval.{version}.json"
    if not path.is_file():
        raise SupplierVolumeEvalError(f"Датасет supplier_volume_eval.{version} не найден.")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("dataset_version") != version:
        raise SupplierVolumeEvalError("Версия датасета не совпадает с именем файла.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SupplierVolumeEvalError("Датасет не содержит примеров.")
    ids: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or case_id in ids:
            raise SupplierVolumeEvalError("ID примеров должны быть уникальными строками.")
        ids.add(case_id)
        if case.get("category") not in _CATEGORIES:
            raise SupplierVolumeEvalError(f"Неизвестная категория примера {case_id}.")
        if case.get("expected_status") not in {"compatible", "incompatible", "unknown"}:
            raise SupplierVolumeEvalError(f"Некорректный expected_status у {case_id}.")
        if not isinstance(case.get("expected_shortlist"), bool):
            raise SupplierVolumeEvalError(f"Нет expected_shortlist у {case_id}.")
    return dataset


def run_supplier_volume_eval(version: str = "v1") -> dict[str, Any]:
    dataset = load_supplier_volume_dataset(version)
    reports: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        compatibility = assess_supply_volume(
            case["page_text"],
            case["requested_volume"],
            source_url=f"https://synthetic.example/{case['id']}",
        )
        score = score_supplier(
            {
                "supplier_type": "manufacturer",
                "cas_status": "confirmed",
                "volume_compatibility": compatibility,
            },
            _STRONG_EVIDENCE,
        )
        actual_shortlist = score.shortlist_eligible
        expected_shortlist = case["expected_shortlist"]
        reports.append(
            {
                "id": case["id"],
                "category": case["category"],
                "expected_status": case["expected_status"],
                "actual_status": compatibility["status"],
                "expected_shortlist": expected_shortlist,
                "actual_shortlist": actual_shortlist,
                "false_accept": actual_shortlist and not expected_shortlist,
                "passed": (
                    compatibility["status"] == case["expected_status"]
                    and actual_shortlist == expected_shortlist
                ),
            }
        )
    laboratory = [
        report for report in reports if report["category"] == "laboratory_negative"
    ]
    false_accepts = sum(report["false_accept"] for report in laboratory)
    return {
        "dataset_version": dataset["dataset_version"],
        "metrics": {
            "case_count": len(reports),
            "passed_count": sum(report["passed"] for report in reports),
            "laboratory_supplier_false_accept_rate": round(
                false_accepts / len(laboratory), 4
            ),
        },
        "failed_case_ids": [report["id"] for report in reports if not report["passed"]],
        "cases": reports,
    }
