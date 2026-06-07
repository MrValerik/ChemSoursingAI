"""Тесты оркестратора извлечения (LLM → валидаторы → fallback).

LLM подменяется фейком через параметр llm — реальная модель не нужна.
"""

from app.extraction.llm_client import LLMUnavailableError
from app.extraction.pipeline import extract_quote

EMAIL = "Price USD 12.50/kg CIF Shanghai. MOQ 25 kg. CoA available."


class _Fake:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or {}
        self.fail = fail

    def extract_quote(self, text):
        if self.fail:
            raise LLMUnavailableError("down")
        return self.payload


def test_rules_only_when_llm_disabled():
    r = extract_quote(EMAIL, use_llm=False)
    assert r.method == "rules"
    assert r.price == 12.5
    assert r.incoterm == "CIF"


def test_llm_agreement_boosts_confidence():
    llm = _Fake({"price": 12.5, "currency": "USD", "incoterm": "CIF",
                 "moq": "25 kg", "has_coa": True, "has_tds": False})
    r = extract_quote(EMAIL, llm=llm)
    assert r.method == "llm+rules"
    assert r.field_confidence["price"] == 0.95
    assert r.field_confidence["incoterm"] == 0.95


def test_disagreement_prefers_deterministic_rule():
    # LLM говорит FOB, но в тексте CIF — доверяем правилу, уверенность падает.
    llm = _Fake({"price": 12.5, "currency": "USD", "incoterm": "FOB",
                 "has_coa": True, "has_tds": False})
    r = extract_quote(EMAIL, llm=llm)
    assert r.incoterm == "CIF"
    assert r.field_confidence["incoterm"] == 0.5


def test_invalid_incoterm_rejected_by_validator():
    llm = _Fake({"price": 12.5, "incoterm": "BANANA",
                 "has_coa": False, "has_tds": False})
    r = extract_quote(EMAIL, llm=llm)
    assert r.incoterm == "CIF"  # невалидное значение LLM отброшено


def test_llm_unavailable_falls_back_to_rules():
    r = extract_quote(EMAIL, llm=_Fake(fail=True))
    assert r.method == "rules"
    assert r.price == 12.5
