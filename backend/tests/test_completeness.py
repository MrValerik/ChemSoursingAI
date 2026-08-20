"""Тесты контроля полноты и правил эскалации."""

from app.models.enums import EscalationReason
from app.services.completeness import accumulate_quotations, evaluate_completeness
from app.services.escalation_rules import detect_escalation


def _full_quote():
    return {
        "price": 12.5,
        "currency": "USD",
        "incoterm": "CIP",
        "moq": "25 kg",
        "has_coa": True,
        "has_tds": True,
        "grade": "USP",
        "payment_terms": "T/T",
        "lead_time": "15 days",
    }


def test_complete_quote():
    r = evaluate_completeness(_full_quote())
    assert r.is_complete
    assert r.missing_fields == []


def test_missing_price_and_moq():
    q = _full_quote()
    q["price"] = None
    q["moq"] = "  "
    r = evaluate_completeness(q)
    assert not r.is_complete
    assert "price" in r.missing_fields
    assert "moq" in r.missing_fields


def test_missing_specification():
    q = _full_quote()
    q["has_coa"] = False
    q["has_tds"] = False
    r = evaluate_completeness(q)
    assert "specification" in r.missing_fields


def test_low_confidence_breaks_completeness():
    r = evaluate_completeness(
        _full_quote(), field_confidence={"price": 0.4, "incoterm": 0.95}
    )
    assert not r.is_complete
    assert r.low_confidence_fields == ["price"]


def test_low_confidence_in_commercial_terms_blocks_complete_quote():
    r = evaluate_completeness(
        _full_quote(), field_confidence={"lead_time": 0.2}
    )
    assert not r.is_complete
    assert r.low_confidence_fields == ["lead_time"]


def test_accumulates_confirmed_fields_across_replies():
    result = accumulate_quotations(
        [
            {
                "price": 12.5,
                "currency": "USD",
                "incoterm": "CIP",
                "moq": None,
                "grade": "USP",
                "payment_terms": None,
                "lead_time": None,
                "has_coa": False,
                "has_tds": False,
                "field_confidence": {"price": 0.9, "incoterm": 0.9},
            },
            {
                "price": None,
                "currency": None,
                "incoterm": None,
                "moq": "25 kg",
                "grade": None,
                "payment_terms": "T/T",
                "lead_time": "15 days",
                "has_coa": True,
                "has_tds": False,
                "field_confidence": {"moq": 0.95},
            },
        ]
    )

    assert result.completeness.is_complete
    assert result.quote["price"] == 12.5
    assert result.quote["moq"] == "25 kg"


def test_escalation_none_for_clean_quote():
    q = _full_quote()
    r = evaluate_completeness(q)
    assert detect_escalation(q, r) is None


def test_escalation_low_confidence():
    q = _full_quote()
    r = evaluate_completeness(q, field_confidence={"price": 0.3})
    assert detect_escalation(q, r) is EscalationReason.LOW_CONFIDENCE


def test_escalation_custom_synthesis():
    q = _full_quote()
    q["lead_time"] = "Custom synthesis, 6-8 weeks"
    r = evaluate_completeness(q)
    assert detect_escalation(q, r) is EscalationReason.CUSTOM_SYNTHESIS


def test_escalation_shortage_from_free_text():
    q = _full_quote()
    r = evaluate_completeness(q)
    assert detect_escalation(q, r, free_text="Sorry, currently out of stock") is (
        EscalationReason.SHORTAGE
    )
