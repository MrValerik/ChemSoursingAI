"""Regression metric for laboratory suppliers in industrial RFQs."""

from app.eval.supplier_volume import (
    load_supplier_volume_dataset,
    run_supplier_volume_eval,
)


def test_supplier_volume_dataset_is_versioned_and_synthetic():
    dataset = load_supplier_volume_dataset("v1")
    assert dataset["dataset_version"] == "v1"
    assert {case["category"] for case in dataset["cases"]} == {
        "laboratory_negative",
        "industrial_positive",
        "unknown_evidence",
    }


def test_laboratory_supplier_false_accept_rate_is_zero():
    report = run_supplier_volume_eval("v1")
    assert report["failed_case_ids"] == []
    assert report["metrics"]["passed_count"] == report["metrics"]["case_count"]
    assert report["metrics"]["laboratory_supplier_false_accept_rate"] == 0


def test_supplier_volume_eval_is_deterministic():
    assert run_supplier_volume_eval("v1") == run_supplier_volume_eval("v1")
