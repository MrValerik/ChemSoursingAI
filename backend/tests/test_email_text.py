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
