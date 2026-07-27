import json
from pathlib import Path

from app.services.supplier_scoring import score_supplier


def test_supplier_scoring_regression_dataset():
    fixture_path = Path(__file__).parent / "fixtures" / "supplier_scoring_eval.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert {case["name"] for case in cases} == {
        "verified_manufacturer",
        "documented_distributor",
        "cas_contradiction",
    }

    for case in cases:
        score = score_supplier(case["assessment"], case["evidence"])
        expected = case["expected"]
        assert score.shortlist_eligible is expected["shortlist_eligible"], case["name"]
        assert score.hard_exclusion is expected["hard_exclusion"], case["name"]
        if "min_score" in expected:
            assert score.total >= expected["min_score"], case["name"]
        if "max_score" in expected:
            assert score.total <= expected["max_score"], case["name"]


def test_unverified_quotes_never_add_points():
    score = score_supplier(
        {"supplier_type": "manufacturer", "cas_status": "confirmed"},
        [
            {
                "claim_type": "chemical_identity",
                "support_status": "supports",
                "quote_verified": False,
            },
            {
                "claim_type": "manufacturer_role",
                "support_status": "supports",
                "quote_verified": False,
            },
        ],
    )
    assert score.total == 0
    assert score.shortlist_eligible is False
