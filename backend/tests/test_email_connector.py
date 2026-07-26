"""Разбор Email без сети: кодировки, HTML и метаданные вложений."""

from email.message import EmailMessage

from app.connectors.email import parse_email


def test_parse_email_extracts_safe_text_and_attachments():
    message = EmailMessage()
    message["From"] = "Sales <sales@supplier.cn>"
    message["To"] = "buyer@example.com"
    message["Subject"] = "Re: [RFQ-42] Аспирин"
    message["Message-ID"] = "<reply-42@supplier.cn>"
    message["In-Reply-To"] = "<request-42@example.com>"
    message.set_content("Price USD 12/kg, CIP Moscow. MOQ 1 MT.")
    message.add_attachment(
        b"test-pdf",
        maintype="application",
        subtype="pdf",
        filename="CoA.pdf",
    )

    parsed = parse_email(message.as_bytes(), uid="101")

    assert parsed.uid == "101"
    assert parsed.message_id == "<reply-42@supplier.cn>"
    assert parsed.from_address == "sales@supplier.cn"
    assert parsed.subject == "Re: [RFQ-42] Аспирин"
    assert "USD 12/kg" in parsed.text
    assert parsed.in_reply_to == "<request-42@example.com>"
    assert parsed.attachments == [
        {
            "filename": "CoA.pdf",
            "content_type": "application/pdf",
            "size": 8,
        }
    ]


def test_parse_email_removes_html_scripts():
    message = EmailMessage()
    message["From"] = "supplier@example.com"
    message["To"] = "buyer@example.com"
    message["Subject"] = "[RFQ-7] Quote"
    message.set_content(
        "<p>Price: USD 10/kg</p><script>ignore_instruction()</script>",
        subtype="html",
    )

    parsed = parse_email(message.as_bytes(), uid="102")

    assert "Price: USD 10/kg" in parsed.text
    assert "ignore_instruction" not in parsed.text
