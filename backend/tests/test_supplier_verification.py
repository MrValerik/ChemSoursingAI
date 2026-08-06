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


def test_a_zero_self_score_does_not_block_a_confirmed_candidate():
    """Балл аудитора — сведение для человека, а не условие ворот.

    Промпт не объяснял шкалу, и модель ставила ноль во всех 102 оценках.
    Пока балл входил в условия, короткий список не открылся ни разу:
    двенадцати кандидатам мешал только он.
    """
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="manufacturer",
        verification_status="confirmed",
        recommended_action="shortlist",
        confidence=0,
        reason="CAS и собственное производство подтверждены цитатами.",
        supporting_claim_ids=[11, 12],
        contradictory_claim_ids=[],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    assert result["shortlist_eligible"] is True
    assert result["verification"]["confidence"] == 0


def test_structural_conditions_still_block_the_shortlist():
    """Снят один барьер, а не все: роль по-прежнему обязана быть доказана."""
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="distributor",
        verification_status="confirmed",
        recommended_action="shortlist",
        confidence=95,
        reason="Компания перепродаёт чужой продукт.",
        supporting_claim_ids=[11, 12],
        contradictory_claim_ids=[],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    assert result["shortlist_eligible"] is False
    assert "роль производителя не подтверждена" in (
        result["verification"]["gate_reason"]
    )


def test_the_auditors_own_verdict_is_named_in_the_reason():
    """Иначе закупщик видит отказ без причины.

    У Anhui Liwei в прогоне 60 прошли все структурные проверки: вещество
    exact, роль manufacturer, цитаты обоих обязательных типов на месте.
    Аудитор выбрал needs_review — и это единственное, что закрыло ворота,
    но в объяснении не было ни слова: «Короткий список заблокирован до
    ручной проверки».
    """
    verification = SupplierVerification(
        result_index=0,
        substance_match="exact",
        supplier_role="manufacturer",
        verification_status="needs_review",
        recommended_action="manual_review",
        confidence=80,
        reason="Страница не показывает подтверждения собственного завода.",
        supporting_claim_ids=[11, 12],
        contradictory_claim_ids=[],
        missing_evidence=[],
    )

    result = apply_supplier_verification(
        _base_result(), verification, _evidence()
    )

    gate_reason = result["verification"]["gate_reason"]
    assert result["shortlist_eligible"] is False
    assert "needs_review" in gate_reason
    assert "manual_review" in gate_reason


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
