"""Разбор Email без сети: кодировки, HTML и метаданные вложений."""

from email.message import EmailMessage
from types import SimpleNamespace

from app.connectors.email import EmailConnector, parse_email
from app.extraction.email_text import latest_reply_text


def test_parse_email_extracts_safe_text_and_attachments():
    message = EmailMessage()
    message["From"] = "Sales <sales@supplier.cn>"
    message["To"] = "buyer@example.com"
    message["Subject"] = "Re: [RFQ-42] Аспирин"
    message["Message-ID"] = "<reply-42@supplier.cn>"
    message["In-Reply-To"] = "<request-42@example.com>"
    message["Date"] = "Thu, 20 Feb 2020 12:00:00 +0000"
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
    assert parsed.message_at is not None
    assert parsed.message_at.isoformat() == "2020-02-20T12:00:00+00:00"
    assert parsed.was_seen is False
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


def test_parse_html_email_preserves_boundaries_for_quoted_history_cleanup():
    message = EmailMessage()
    message["From"] = "supplier@example.com"
    message["To"] = "buyer@example.com"
    message["Subject"] = "Re: [RFQ-7] Quote"
    message.set_content(
        "<div>Price: USD 10/kg</div>"
        "<div class='gmail_quote'>"
        "On Fri, 11 Sep 2026 at 10:15, ChemSource "
        "&lt;buyer@example.com&gt; wrote:<br>"
        "Please provide your price."
        "</div>",
        subtype="html",
    )

    parsed = parse_email(message.as_bytes(), uid="html-quoted")

    assert "\n" in parsed.text
    assert latest_reply_text(parsed.text) == "Price: USD 10/kg"


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


def test_fetch_recent_includes_seen_and_unseen_messages(monkeypatch):
    def raw_message(message_id: str) -> bytes:
        message = EmailMessage()
        message["From"] = "supplier@example.com"
        message["To"] = "buyer@example.com"
        message["Subject"] = "Re: [RFQ-42] Quote"
        message["Message-ID"] = message_id
        message.set_content("Price USD 10/kg")
        return message.as_bytes()

    raw = {
        b"10": raw_message("<seen@example.com>"),
        b"11": raw_message("<unseen@example.com>"),
    }

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            return "OK", []

        def select(self, folder, readonly=False):
            assert readonly is False
            return "OK", [b"2"]

        def uid(self, command, *args):
            if command == "search":
                criterion = str(args[-1])
                if criterion == "UNSEEN":
                    return "OK", [b"11"]
                assert "SINCE" in criterion
                return "OK", [b"10 11"]
            if command == "fetch":
                uid = args[0]
                key = uid.encode() if isinstance(uid, str) else uid
                return "OK", [(b"RFC822", raw[key])]
            raise AssertionError(f"Unexpected IMAP command: {command}")

        def logout(self):
            return "BYE", []

    monkeypatch.setattr("app.connectors.email.imaplib.IMAP4_SSL", FakeImap)
    settings = SimpleNamespace(
        imap_host="imap.example.com",
        imap_port=993,
        imap_user="buyer@example.com",
        imap_password="secret",
        imap_use_ssl=True,
        imap_folder="INBOX",
        email_timeout_s=30,
    )

    messages = EmailConnector(settings).fetch_recent(limit=100)

    assert [message.uid for message in messages] == ["10", "11"]
    assert [message.was_seen for message in messages] == [True, False]


def test_fetch_unseen_returns_newer_messages_before_older_uid_timeout(monkeypatch):
    def raw_message(message_id: str) -> bytes:
        message = EmailMessage()
        message["From"] = "supplier@example.com"
        message["To"] = "buyer@example.com"
        message["Subject"] = "Quote"
        message["Message-ID"] = message_id
        message.set_content("Price USD 10/kg")
        return message.as_bytes()

    raw = {
        b"11": raw_message("<eleven@example.com>"),
        b"12": raw_message("<twelve@example.com>"),
    }

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            return "OK", []

        def select(self, folder, readonly=False):
            return "OK", [b"3"]

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"10 11 12"]
            if command == "fetch":
                uid = args[0]
                key = uid.encode() if isinstance(uid, str) else uid
                if key == b"10":
                    raise TimeoutError("old message timed out")
                return "OK", [(b"RFC822", raw[key])]
            raise AssertionError(f"Unexpected IMAP command: {command}")

        def logout(self):
            return "BYE", []

    monkeypatch.setattr("app.connectors.email.imaplib.IMAP4_SSL", FakeImap)
    settings = SimpleNamespace(
        imap_host="imap.example.com",
        imap_port=993,
        imap_user="buyer@example.com",
        imap_password="secret",
        imap_use_ssl=True,
        imap_folder="INBOX",
        email_timeout_s=30,
    )

    messages = EmailConnector(settings).fetch_unseen(limit=100)

    assert [message.uid for message in messages] == ["11", "12"]


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
