"""Безопасный SMTP/IMAP-коннектор для корпоративной почты."""

from __future__ import annotations

import hashlib
import html
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

from app.core.config import Settings, get_settings


class EmailConfigurationError(RuntimeError):
    """Канал не настроен или включён с противоречивыми параметрами."""


class EmailDeliveryError(RuntimeError):
    """Ошибка соединения, отправки или чтения почты."""


@dataclass(slots=True)
class IncomingEmail:
    """Нормализованное входящее письмо без исполнения HTML-содержимого."""

    uid: str
    message_id: str
    subject: str
    from_address: str
    to_addresses: list[str]
    text: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def _plain_text(message) -> str:
    """Возвращает text/plain, а при его отсутствии — очищенный HTML."""
    plain: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if not isinstance(content, str):
            continue
        if content_type == "text/plain":
            plain.append(content)
        else:
            html_parts.append(content)
    if plain:
        return "\n".join(plain).strip()
    raw_html = "\n".join(html_parts)
    # HTML — недоверенные данные. Сохраняем только текст без тегов/скриптов.
    raw_html = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw_html)).split())


def parse_email(raw: bytes, uid: str) -> IncomingEmail:
    """Разбирает RFC822-письмо в безопасную структуру."""
    message = message_from_bytes(raw, policy=policy.default)
    raw_message_id = str(message.get("Message-ID") or "").strip()
    message_id = raw_message_id or (
        f"<sha256-{hashlib.sha256(raw).hexdigest()}@chemsource.local>"
    )
    references = [
        value
        for value in str(message.get("References") or "").split()
        if value.startswith("<") and value.endswith(">")
    ]
    attachments: list[dict] = []
    for part in message.walk():
        filename = part.get_filename()
        if not filename:
            continue
        # Почтовые клиенты прячут логотипы, подписи и другие встроенные
        # изображения внутри HTML-письма. У них может быть имя файла, поэтому
        # одной проверки filename недостаточно: такие MIME-части не являются
        # файлами, которые поставщик сознательно приложил к письму.
        disposition = (part.get_content_disposition() or "").lower()
        content_id = str(part.get("Content-ID") or "").strip()
        if disposition == "inline" or (
            content_id and part.get_content_maintype().lower() == "image"
        ):
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": _decode_header(filename),
                "content_type": part.get_content_type(),
                "size": len(payload),
                # Содержимое нужно, чтобы сохранить паспорт качества и прочитать
                # его. Оно не попадает в JSON коммуникации: слой workflow
                # заменяет его ссылкой на сохранённый документ.
                "content": payload,
            }
        )
    return IncomingEmail(
        uid=uid,
        message_id=message_id,
        subject=_decode_header(str(message.get("Subject") or "")),
        from_address=parseaddr(str(message.get("From") or ""))[1].lower(),
        to_addresses=[
            parseaddr(value.strip())[1].lower()
            for value in str(message.get("To") or "").split(",")
            if parseaddr(value.strip())[1]
        ],
        text=_plain_text(message),
        in_reply_to=str(message.get("In-Reply-To") or "").strip() or None,
        references=references,
        attachments=attachments,
    )


class EmailConnector:
    """Отправляет SMTP-письма и читает непросмотренные IMAP-сообщения."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def smtp_configured(self) -> bool:
        s = self.settings
        return bool(s.smtp_host and s.smtp_user and s.smtp_password and s.email_from)

    @property
    def imap_configured(self) -> bool:
        s = self.settings
        return bool(s.imap_host and s.imap_user and s.imap_password)

    def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        message_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> str:
        if not self.smtp_configured:
            raise EmailConfigurationError(
                "SMTP не настроен: заполните SMTP_* и EMAIL_FROM"
            )
        s = self.settings
        recipient = parseaddr(to_address)[1].strip()
        if not recipient or "@" not in recipient:
            raise EmailConfigurationError("Некорректный адрес получателя Email")
        safe_subject = " ".join(subject.replace("\r", " ").splitlines()).strip()
        if not safe_subject:
            safe_subject = "RFQ"
        outbound_message_id = (message_id or "").strip()
        if outbound_message_id:
            if (
                "\r" in outbound_message_id
                or "\n" in outbound_message_id
                or not outbound_message_id.startswith("<")
                or not outbound_message_id.endswith(">")
            ):
                raise EmailConfigurationError("Некорректный Message-ID")
        else:
            outbound_message_id = make_msgid(
                domain=(s.email_from.split("@")[-1] or None)
            )
        message = EmailMessage()
        message["From"] = formataddr((s.email_from_name, s.email_from))
        message["To"] = recipient
        message["Subject"] = safe_subject[:998]
        message["Message-ID"] = outbound_message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        refs = [ref for ref in (references or []) if ref]
        if in_reply_to and in_reply_to not in refs:
            refs.append(in_reply_to)
        if refs:
            message["References"] = " ".join(refs)
        message.set_content(body)
        for attachment in attachments or []:
            payload = attachment.get("content")
            if not isinstance(payload, (bytes, bytearray)):
                raise EmailConfigurationError(
                    "Содержимое исходящего вложения недоступно"
                )
            content_type = str(
                attachment.get("content_type") or "application/octet-stream"
            ).split(";", 1)[0]
            maintype, _, subtype = content_type.partition("/")
            if not maintype or not subtype:
                maintype, subtype = "application", "octet-stream"
            filename = Path(
                str(attachment.get("filename") or "document").replace("\\", "/")
            ).name
            filename = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip()
            message.add_attachment(
                bytes(payload),
                maintype=maintype,
                subtype=subtype,
                filename=(filename[:200] or "document"),
            )

        try:
            context = ssl.create_default_context()
            if s.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    s.smtp_host, s.smtp_port, timeout=s.email_timeout_s, context=context
                ) as client:
                    client.login(s.smtp_user, s.smtp_password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(
                    s.smtp_host, s.smtp_port, timeout=s.email_timeout_s
                ) as client:
                    client.ehlo()
                    if s.smtp_starttls:
                        client.starttls(context=context)
                        client.ehlo()
                    client.login(s.smtp_user, s.smtp_password)
                    client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(f"Не удалось отправить Email: {exc}") from exc
        return outbound_message_id

    def check_connections(self) -> dict[str, bool]:
        """Проверяет SMTP и IMAP аутентификацией без отправки письма."""
        if not self.smtp_configured or not self.imap_configured:
            raise EmailConfigurationError(
                "Email не настроен: заполните SMTP, IMAP и адрес отправителя"
            )
        smtp = self._smtp_client()
        try:
            smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.noop()
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(
                "SMTP не подтвердил подключение или учётные данные"
            ) from exc
        finally:
            try:
                smtp.quit()
            except (OSError, smtplib.SMTPException):
                pass

        imap = self._imap_client()
        try:
            imap.login(self.settings.imap_user, self.settings.imap_password)
            status, _ = imap.select(self.settings.imap_folder, readonly=True)
            if status != "OK":
                raise EmailDeliveryError(
                    f"IMAP не открыл папку {self.settings.imap_folder!r}"
                )
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailDeliveryError(
                "IMAP не подтвердил подключение или учётные данные"
            ) from exc
        finally:
            try:
                imap.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        return {"smtp": True, "imap": True}

    def fetch_unseen(self, limit: int = 20) -> list[IncomingEmail]:
        if not self.imap_configured:
            raise EmailConfigurationError("IMAP не настроен: заполните IMAP_*")
        s = self.settings
        client = self._imap_client()
        try:
            client.login(s.imap_user, s.imap_password)
            status, _ = client.select(s.imap_folder, readonly=False)
            if status != "OK":
                raise EmailDeliveryError(
                    f"IMAP не открыл папку {s.imap_folder!r}"
                )
            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise EmailDeliveryError("IMAP не выполнил поиск непрочитанных писем")
            uids = (data[0] or b"").split()[-max(1, limit) :]
            messages: list[IncomingEmail] = []
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii", errors="ignore")
                status, chunks = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                raw = next(
                    (
                        chunk[1]
                        for chunk in chunks
                        if isinstance(chunk, tuple) and isinstance(chunk[1], bytes)
                    ),
                    None,
                )
                if raw:
                    messages.append(parse_email(raw, uid))
            return messages
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailDeliveryError(f"Не удалось прочитать IMAP: {exc}") from exc
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def mark_seen(self, uids: list[str]) -> None:
        """Помечает только успешно обработанные письма."""
        if not uids:
            return
        s = self.settings
        client = self._imap_client()
        try:
            client.login(s.imap_user, s.imap_password)
            client.select(s.imap_folder, readonly=False)
            for uid in uids:
                client.uid("store", uid, "+FLAGS", "(\\Seen)")
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailDeliveryError(
                f"Письма обработаны, но IMAP не обновил флаг Seen: {exc}"
            ) from exc
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def _imap_client(self):
        s = self.settings
        try:
            if s.imap_use_ssl:
                return imaplib.IMAP4_SSL(
                    s.imap_host,
                    s.imap_port,
                    ssl_context=ssl.create_default_context(),
                    timeout=s.email_timeout_s,
                )
            return imaplib.IMAP4(
                s.imap_host,
                s.imap_port,
                timeout=s.email_timeout_s,
            )
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailDeliveryError(f"Не удалось подключиться к IMAP: {exc}") from exc

    def _smtp_client(self):
        s = self.settings
        try:
            context = ssl.create_default_context()
            if s.smtp_use_ssl:
                return smtplib.SMTP_SSL(
                    s.smtp_host,
                    s.smtp_port,
                    timeout=s.email_timeout_s,
                    context=context,
                )
            client = smtplib.SMTP(
                s.smtp_host, s.smtp_port, timeout=s.email_timeout_s
            )
            client.ehlo()
            if s.smtp_starttls:
                client.starttls(context=context)
                client.ehlo()
            return client
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(
                "Не удалось подключиться к SMTP"
            ) from exc
