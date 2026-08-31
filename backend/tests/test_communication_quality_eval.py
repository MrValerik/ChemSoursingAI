import pytest

from app.eval.communication_quality import load_cases, score_reply


def test_versioned_eval_covers_quality_and_security_without_real_data():
    cases = load_cases()
    assert len(cases) == 12
    assert {"injection_cannot_place_order", "payment_amended", "multilingual_supplier"} <= {c["id"] for c in cases}


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_scoring_rejects_empty_or_overlong_replies(case):
    assert score_reply(case, "Unnecessary long checklist. " * 100)
    if case["must_match"]:
        assert score_reply(case, "")


def test_packaging_eval_checks_an_actual_answer_not_just_absence_of_forbidden_words():
    case = next(c for c in load_cases() if c["id"] == "buyer_packaging_unknown")
    assert not score_reply(case, "The packaging choice needs internal confirmation before you quote.")
    assert score_reply(case, "We require 200 L returnable containers.")
