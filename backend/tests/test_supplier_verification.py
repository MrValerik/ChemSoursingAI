from app.schemas.supplier_verification import SupplierVerification
from app.services.supplier_verification import apply_supplier_verification


def _base_result() -> dict:
    return {
        "result_index": 0,
        "shortlist_eligible": True,
        "red_flags": [],
        "missing_evidence": [],
    }


def _evidence() -> list[dict]:
    return [
        {
            "id": 11,
            "claim_type": "chemical_identity",
            "support_status": "supports",
            "quote_verified": True,
        },
        {
            "id": 12,
            "claim_type": "manufacturer_role",
            "support_status": "supports",
            "quote_verified": True,
        },
        {
            "id": 13,
            "claim_type": "chemical_identity",
            "support_status": "contradicts",
            "quote_verified": True,
        },
    ]


def test_verifier_confirms_only_with_required_verified_claims():
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="manufacturer",
        verification_status="confirmed",
        recommended_action="shortlist",
        confidence=86,
        reason="CAS и собственное производство подтверждены.",
        supporting_claim_ids=[11, 12],
        contradictory_claim_ids=[],
        missing_evidence=["Актуальный CoA"],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    assert result["shortlist_eligible"] is True
    assert result["verification"]["status"] == "confirmed"
    assert result["verification"]["supporting_claim_ids"] == [11, 12]
    assert result["missing_evidence"] == ["Актуальный CoA"]


def test_verifier_rejects_substance_mismatch():
    verification = SupplierVerification(
        result_index=0,
        substance_match="mismatch",
        supplier_role="manufacturer",
        verification_status="rejected",
        recommended_action="reject",
        confidence=91,
        reason="На странице указан другой CAS.",
        supporting_claim_ids=[12],
        contradictory_claim_ids=[13],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    assert result["shortlist_eligible"] is False
    assert result["verification"]["status"] == "rejected"
    assert result["verification"]["contradictory_claim_ids"] == [13]
    assert any("Решение аудитора" in flag for flag in result["red_flags"])


def test_verifier_cannot_confirm_with_invalid_or_wrong_kind_claim_ids():
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="manufacturer",
        verification_status="confirmed",
        recommended_action="shortlist",
        confidence=90,
        reason="Кандидат выглядит подходящим.",
        supporting_claim_ids=[11, 13, 999],
        contradictory_claim_ids=[],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    assert result["shortlist_eligible"] is False
    assert result["verification"]["status"] == "needs_review"
    assert result["verification"]["supporting_claim_ids"] == [11]
    assert result["verification"]["invalid_claim_ids"] == [13, 999]


def test_verifier_cannot_confirm_with_unverified_quote():
    evidence = _evidence()
    evidence[1]["quote_verified"] = False
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="manufacturer",
        verification_status="confirmed",
        recommended_action="shortlist",
        confidence=90,
        reason="CAS и производство заявлены на странице.",
        supporting_claim_ids=[11, 12],
        contradictory_claim_ids=[],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, evidence
    )

    assert result["shortlist_eligible"] is False
    assert result["verification"]["status"] == "needs_review"
    assert result["verification"]["supporting_claim_ids"] == [11]
    assert result["verification"]["invalid_claim_ids"] == [12]


def test_unavailable_verifier_safely_blocks_shortlist():
    result = apply_supplier_verification(
        _base_result(),
        None,
        _evidence(),
        unavailable_reason="Локальная модель не ответила.",
    )

    assert result["shortlist_eligible"] is False
    assert result["verification"]["status"] == "unavailable"
    assert result["verification"]["reason"] == "Локальная модель не ответила."
    assert "Независимая проверка кандидата" in result["missing_evidence"]
