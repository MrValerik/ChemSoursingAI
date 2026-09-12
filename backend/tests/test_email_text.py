"""Регрессии очистки процитированной Email-цепочки без изменения оригинала."""

from app.extraction.email_text import latest_reply_text
from app.extraction.pipeline import extract_quote


def test_latest_reply_excludes_forwarded_rfq_requirements():
    original = (
        "Dear Manager:\r\n"
        "Price: USD 6.8/KG by sea FOB Shanghai for 500KG\r\n"
        "Package: 25KG/Bag\r\n\r\n"
        "发件人： ChemSource\r\n"
        "主题： [RFQ-30] Request for quotation\r\n"
        "Please quote CIP, FCA and EXW.\r\n"
        "Required documents: CoA and TDS\r\n"
    )

    latest = latest_reply_text(original)
    quote = extract_quote(latest, use_llm=False)

    assert "发件人" not in latest
    assert quote.price == 6.8
    assert quote.incoterm == "FOB"
    assert quote.quoted_quantity == "500KG"
    assert quote.has_coa is False
    assert quote.has_tds is False


def test_latest_reply_removes_gmail_quoted_tail():
    text = "MOQ is 25 kg.\n\n> Previous message\n> Please provide MOQ and CoA."
    assert latest_reply_text(text) == "MOQ is 25 kg."


def test_latest_reply_removes_wrapped_gmail_history_marker():
    text = (
        "Our price is USD 10/kg.\n\n"
        "On Fri, 11 Sep 2026 at 10:15, ChemSource\n"
        "<buyer@example.com> wrote:\n"
        "Please provide your price and lead time."
    )

    assert latest_reply_text(text) == "Our price is USD 10/kg."


def test_latest_reply_removes_russian_outlook_headers():
    text = (
        "Цена 900 рублей за кг.\n\n"
        "От: ChemSource <buyer@example.com>\n"
        "Отправлено: пятница, 11 сентября 2026 г.\n"
        "Тема: RFQ\n"
        "Просим сообщить цену."
    )

    assert latest_reply_text(text) == "Цена 900 рублей за кг."
