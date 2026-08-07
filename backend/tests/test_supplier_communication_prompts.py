"""Регрессии правил общения, обобщённых из закупочных переписок."""

import pytest

from app.services.communication_testing import (
    _message_language_matches,
    _plain_text_message,
)
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
        "как живой сотрудник отдела закупок",
        "Никогда не используй Markdown",
        "один–три наиболее важных связанных вопроса",
        "Начинай по делу",
        "Не добавляй в тело строку «Subject»",
        "служебные оговорки о тесте",
        "включая «как ваши дела»",
        "обрабатывать сотрудник после эскалации",
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
    assert "50–100 слов" in CHANNEL_INSTRUCTIONS["email"]
    assert "1–4 короткие строки" in CHANNEL_INSTRUCTIONS["whatsapp"]
    assert "сразу переходи к сути" in CHANNEL_INSTRUCTIONS["email"]
    assert "без долгого приветствия" in CHANNEL_INSTRUCTIONS["whatsapp"]
    assert "dear friend" in CHANNEL_INSTRUCTIONS["whatsapp"]
    assert "без Markdown" in CHANNEL_INSTRUCTIONS["email"]
    assert "звёздочки" in CHANNEL_INSTRUCTIONS["whatsapp"]


def test_plain_text_message_removes_markdown_without_damaging_product_text():
    generated = """**Subject: Quotation request**
**Hello, Anna.**
* Please quote X-100, 2M solution.
* Please send the `CoA`.*



Thank you.
This is a test message.
"""

    result = _plain_text_message(generated)

    assert result == (
        "Hello, Anna.\n"
        "Please quote X-100, 2M solution.\n"
        "Please send the CoA.\n\n"
        "Thank you."
    )
    assert "*" not in result


@pytest.mark.parametrize(
    ("generated", "language", "expected"),
    [
        ("Здравствуйте, сообщите цену 2000 USD/kg и пришлите CoA.", "ru", True),
        ("Hello, please provide your price and lead time.", "ru", False),
        ("Hello, please quote аммиак CAS 7664-41-7.", "en", True),
        ("Здравствуйте, пришлите цену и срок поставки.", "en", False),
        ("您好，请提供价格和交货期。", "zh", True),
        ("Hello, please provide your price.", "zh", False),
    ],
)
def test_message_language_matches_selected_script(generated, language, expected):
    assert _message_language_matches(generated, language) is expected


@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        (
            "Тема письма: Запрос цены\n\nЗдравствуйте. Нужна цена.\n"
            "Это сообщение создано в тестовом режиме.",
            "Здравствуйте. Нужна цена.",
        ),
        (
            "Subject - RFQ\nHello. Please confirm MOQ. For testing purposes only.",
            "Hello. Please confirm MOQ.",
        ),
        (
            "主题：询价\n您好。请确认最小起订量。\n这是测试消息。",
            "您好。请确认最小起订量。",
        ),
    ],
)
def test_plain_text_message_removes_subject_and_trailing_test_note(
    generated, expected
):
    assert _plain_text_message(generated) == expected
