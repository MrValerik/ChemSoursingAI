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


def _claim(claim_type: str) -> dict:
    return {
        "claim_type": claim_type,
        "support_status": "supports",
        "quote_verified": True,
    }


def _manufacturer_assessment() -> dict:
    return {"supplier_type": "manufacturer", "cas_status": "confirmed"}


def test_self_declared_manufacturer_alone_does_not_reach_the_shortlist():
    """«Мы производитель» на сайте продавца пишет и завод, и перекупщик.

    Замер на стенде: кандидат получил допуск в короткий список на одной
    цитате с собственной маркетинговой страницы, притом что модель сама
    отметила «заявление о производстве требует независимой проверки».
    """
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
    ]
    score = score_supplier(_manufacturer_assessment(), evidence)

    assert score.shortlist_eligible is False
    assert score.hard_exclusion is False
    assert score.total > 0, "кандидат остаётся в выдаче, просто не в шортлисте"


def test_certificate_corroborates_a_manufacturer_claim():
    """Сертификат выдаёт внешний орган — это вторая, независимая опора."""
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("iso"),
    ]
    score = score_supplier(_manufacturer_assessment(), evidence)
    assert score.shortlist_eligible is True


def test_batch_document_also_corroborates():
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("coa"),
    ]
    assert score_supplier(_manufacturer_assessment(), evidence).shortlist_eligible


def test_corroboration_does_not_rescue_a_distributor():
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("gmp"),
        _claim("coa"),
    ]
    score = score_supplier(
        {"supplier_type": "distributor", "cas_status": "confirmed"}, evidence
    )
    assert score.shortlist_eligible is False


def test_corroboration_does_not_override_a_substance_mismatch():
    evidence = [
        _claim("manufacturer_role"),
        _claim("gmp"),
        {
            "claim_type": "chemical_identity",
            "support_status": "contradicts",
            "quote_verified": True,
        },
    ]
    score = score_supplier(_manufacturer_assessment(), evidence)
    assert score.hard_exclusion is True
    assert score.shortlist_eligible is False


def test_incompatible_laboratory_packaging_blocks_the_shortlist():
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("iso"),
    ]
    assessment = {
        **_manufacturer_assessment(),
        "volume_compatibility": {
            "status": "incompatible",
            "requested_volume_raw": "500 kg",
        },
    }

    score = score_supplier(assessment, evidence)

    assert score.shortlist_eligible is False
    assert score.hard_exclusion is False
    assert score.volume_adjustment == -20


def test_unknown_packaging_lowers_evidence_without_claiming_incompatibility():
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("iso"),
    ]
    baseline = score_supplier(_manufacturer_assessment(), evidence)
    unknown = score_supplier(
        {
            **_manufacturer_assessment(),
            "volume_compatibility": {
                "status": "unknown",
                "requested_volume_raw": "500 kg",
            },
        },
        evidence,
    )

    assert unknown.total == baseline.total - 5
    assert unknown.volume_adjustment == -5
    assert unknown.hard_exclusion is False


def test_confirmed_industrial_packaging_has_no_score_penalty():
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("iso"),
    ]
    score = score_supplier(
        {
            **_manufacturer_assessment(),
            "volume_compatibility": {
                "status": "compatible",
                "requested_volume_raw": "500 kg",
            },
        },
        evidence,
    )

    assert score.shortlist_eligible is True
    assert score.volume_adjustment == 0
