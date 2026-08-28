"""Разбор Email без сети: кодировки, HTML и метаданные вложений."""

from email.message import EmailMessage
from types import SimpleNamespace

from app.connectors.email import EmailConnector, parse_email


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
    assert parsed.from_name == "Sales"
    assert parsed.subject == "Re: [RFQ-42] Аспирин"
    assert "USD 12/kg" in parsed.text
    assert parsed.in_reply_to == "<request-42@example.com>"
    # Содержимое доходит до слоя workflow: без него паспорт качества нельзя
    # сохранить и прочитать. В JSON коммуникации оно уже не попадает.
    assert parsed.attachments == [
        {
            "filename": "CoA.pdf",
            "content_type": "application/pdf",
            "size": 8,
            "content": b"test-pdf",
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


def test_parse_email_ignores_hidden_inline_images_but_keeps_attached_png():
    message = EmailMessage()
    message["From"] = "supplier@example.com"
    message["To"] = "buyer@example.com"
    message["Subject"] = "Re: [RFQ-30] Quote"
    message.set_content("Please see the attached image.")
    message.add_related(
        b"inline-logo",
        maintype="image",
        subtype="png",
        cid="<signature-logo>",
        filename="1644835792312.png",
        disposition="inline",
    )
    message.add_attachment(
        b"attached-png",
        maintype="image",
        subtype="png",
        filename="product-label.png",
    )

    parsed = parse_email(message.as_bytes(), uid="103")

    assert [item["filename"] for item in parsed.attachments] == [
        "product-label.png"
    ]


def test_send_preserves_explicit_message_id(monkeypatch):
    delivered: list[EmailMessage] = []

    class FakeSmtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def login(self, username, password):
            pass

        def send_message(self, message):
            delivered.append(message)

    monkeypatch.setattr("app.connectors.email.smtplib.SMTP_SSL", FakeSmtp)
    settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="app@example.com",
        smtp_password="secret",
        smtp_use_ssl=True,
        smtp_starttls=False,
        email_from="app@example.com",
        email_from_name="ChemSource AI",
        email_timeout_s=30,
    )

    returned = EmailConnector(settings).send(
        to_address="owner@example.com",
        subject="Feedback",
        body="Message",
        message_id="<feedback-42@example.com>",
        attachments=[
            {
                "filename": "offer.txt",
                "content_type": "text/plain",
                "content": b"price list",
            }
        ],
    )

    assert returned == "<feedback-42@example.com>"
    assert delivered[0]["Message-ID"] == "<feedback-42@example.com>"
    attachment = next(
        part for part in delivered[0].walk() if part.get_filename() == "offer.txt"
    )
    assert attachment.get_content_type() == "text/plain"
    assert attachment.get_payload(decode=True) == b"price list"
