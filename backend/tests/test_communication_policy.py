"""Регрессии policy-gate перед автоматическим ответом поставщику."""

from app.extraction.llm_client import LLMUnavailableError
from app.services.communication_policy import classify_supplier_message


class FakeLlm:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def test_social_question_is_escalated_without_calling_llm():
    llm = FakeLlm(error=AssertionError("LLM must not be called"))

    decision = classify_supplier_message(
        "Hello, how are you?",
        rfq_name="Substance X",
        rfq_cas="123-45-6",
        llm=llm,
    )

    assert decision.auto_reply_allowed is False
    assert decision.category == "social_or_personal"
    assert decision.method == "rule"
    assert llm.calls == []


def test_off_topic_question_is_escalated_without_calling_llm():
    llm = FakeLlm(error=AssertionError("LLM must not be called"))

    decision = classify_supplier_message(
        "What music do you like?",
        rfq_name="Substance X",
        rfq_cas="123-45-6",
        llm=llm,
    )

    assert decision.auto_reply_allowed is False
    assert decision.category == "off_topic"
    assert decision.method == "rule"
    assert llm.calls == []


def test_standard_procurement_message_may_continue_to_auto_reply():
    llm = FakeLlm(
        result={
            "route": "auto_reply",
            "category": "standard_procurement",
            "explanation": "Поставщик сообщил цену и просит подтвердить объём.",
        }
    )

    decision = classify_supplier_message(
        "USD 12/kg CIP Moscow. Please confirm the required quantity.",
        rfq_name="Substance X",
        rfq_cas="123-45-6",
        llm=llm,
    )

    assert decision.auto_reply_allowed is True
    assert decision.method == "llm"
    assert "supplier_message_untrusted" in llm.calls[0]["user_text"]


def test_ambiguous_or_unavailable_classifier_fails_closed():
    malformed = FakeLlm(
        result={
            "route": "auto_reply",
            "category": "off_topic",
            "explanation": "Неоднозначно.",
        }
    )
    unavailable = FakeLlm(error=LLMUnavailableError("offline"))

    malformed_decision = classify_supplier_message(
        "Tell me more.", rfq_name="X", rfq_cas=None, llm=malformed
    )
    unavailable_decision = classify_supplier_message(
        "Please confirm.", rfq_name="X", rfq_cas=None, llm=unavailable
    )

    assert malformed_decision.auto_reply_allowed is False
    assert malformed_decision.method == "safe_fallback"
    assert unavailable_decision.auto_reply_allowed is False
    assert unavailable_decision.method == "safe_fallback"
