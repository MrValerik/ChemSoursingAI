"""Versioned safety eval of the deterministic supplier decision layer."""

from app.eval.supplier_decision import (
    load_dataset,
    run_supplier_decision_eval,
)

_EXPECTED_CATEGORIES = {
    "true_positive",
    "substance_negative",
    "role_negative",
    "process_negative",
}


def test_dataset_v1_is_versioned_and_covers_required_categories():
    dataset = load_dataset("v1")
    assert dataset["dataset_version"] == "v1"
    categories = {case["category"] for case in dataset["cases"]}
    assert categories == _EXPECTED_CATEGORIES
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids))


def test_decision_layer_produces_no_false_accepts():
    report = run_supplier_decision_eval("v1")
    assert report["safety_violations"] == []
    assert report["failed_case_ids"] == []
    assert report["metrics"]["passed_count"] == report["metrics"]["case_count"]
    assert report["metrics"]["substance_false_accept_rate"] == 0
    assert report["metrics"]["manufacturer_false_accept_rate"] == 0
    assert report["metrics"]["shortlist_precision"] == 1


def test_malformed_auditor_response_becomes_abstention():
    report = run_supplier_decision_eval("v1")
    by_id = {case["id"]: case for case in report["cases"]}
    malformed = by_id["malformed-auditor-response"]
    assert malformed["actual_status"] == "unavailable"
    assert malformed["actual_shortlist_eligible"] is False


def test_prompt_injection_cannot_reference_unknown_claims():
    report = run_supplier_decision_eval("v1")
    by_id = {case["id"]: case for case in report["cases"]}
    injected = by_id["prompt-injection-invalid-claims"]
    assert injected["actual_shortlist_eligible"] is False
    assert set(injected["invalid_claim_ids"]) == {999, 1000}


def test_report_is_deterministic_across_runs():
    first = run_supplier_decision_eval("v1")
    second = run_supplier_decision_eval("v1")
    assert first == second
