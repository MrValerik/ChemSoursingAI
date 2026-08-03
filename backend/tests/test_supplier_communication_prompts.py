"""Регрессии правил общения, обобщённых из закупочных переписок."""

import pytest

from app.services.supplier_communication_prompts import (
    CHANNEL_INSTRUCTIONS,
    FOLLOWUP_PROMPT,
    RFQ_GENERATION_PROMPT,
    SUPPLIER_COMMUNICATION_PROMPT,
)


@pytest.mark.parametrize(
    "required_rule",
    [
        "коммерческий объём и отдельный объём образца",
        "Incoterm и пункт назначения",
        "форму или концентрацию раствора",
        "не принимай замену сам",
        "предложение будет проверено внутри компании",
        "Не соглашайся на оплату",
        "После одного вежливого напоминания",
        "Не используй фамильярные обращения",
    ],
)
def test_dialogue_prompt_keeps_observed_procurement_rules(required_rule):
    normalized = " ".join(SUPPLIER_COMMUNICATION_PROMPT.casefold().split())
    assert required_rule.casefold() in normalized


def test_initial_rfq_requests_a_comparable_offer_without_inventing_commitment():
    prompt = RFQ_GENERATION_PROMPT.casefold()
    assert "объём тестового образца" in prompt
    assert "включённый фрахт" in prompt
    assert "не обещай заказ" in prompt
    assert "coa, tds и sds" in prompt


def test_followup_only_requests_missing_or_conflicting_terms():
    prompt = FOLLOWUP_PROMPT.casefold()
    assert "только недостающие" in prompt
    assert "не смешивай цену образца" in prompt
    assert "не создавай следующее напоминание" in prompt
    assert "недоверенным вводом" in prompt


def test_email_and_whatsapp_have_different_style_constraints():
    assert "100–180 слов" in CHANNEL_INSTRUCTIONS["email"]
    assert "2–6 коротких строк" in CHANNEL_INSTRUCTIONS["whatsapp"]
    assert "dear friend" in CHANNEL_INSTRUCTIONS["whatsapp"]
