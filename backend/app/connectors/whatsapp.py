"""WhatsApp connector with Cloud API and an isolated Web gateway transport."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class WhatsAppConfigurationError(RuntimeError):
    """WhatsApp credentials, gateway, or recipient are missing or malformed."""


class WhatsAppDeliveryError(RuntimeError):
    """The selected WhatsApp transport rejected or could not process a request."""


class WhatsAppConnector:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    @property
    def is_web(self) -> bool:
        return self.settings.whatsapp_transport == "web"

    @property
    def configured(self) -> bool:
        if self.is_web:
            return bool(
                self.settings.whatsapp_web_base_url
                and self.settings.whatsapp_web_service_token
            )
        return bool(
            self.settings.whatsapp_token and self.settings.whatsapp_phone_id
        )

    @property
    def _phone_url(self) -> str:
        base = self.settings.whatsapp_api_base_url.rstrip("/")
        version = self.settings.whatsapp_api_version.strip("/")
        return f"{base}/{version}/{self.settings.whatsapp_phone_id}"

    @property
    def _headers(self) -> dict[str, str]:
        token = (
            self.settings.whatsapp_web_service_token
            if self.is_web
            else self.settings.whatsapp_token
        )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _web_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.whatsapp_web_service_token:
            raise WhatsAppConfigurationError(
                "WhatsApp Web gateway не настроен на сервере"
            )
        try:
            with httpx.Client(
                timeout=self.settings.whatsapp_timeout_s,
                transport=self.transport,
            ) as client:
                response = client.request(
                    method,
                    f"{self.settings.whatsapp_web_base_url.rstrip('/')}{path}",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WhatsAppDeliveryError(
                "WhatsApp Web gateway недоступен или отклонил запрос"
            ) from exc
        if not isinstance(data, dict):
            raise WhatsAppDeliveryError("WhatsApp Web gateway вернул неверный ответ")
        return data

    def web_status(self) -> dict[str, Any]:
        return self._web_request("GET", "/status")

    def web_connect(self) -> dict[str, Any]:
        return self._web_request("POST", "/connect")

    def web_qr(self) -> str:
        data = self._web_request("GET", "/qr")
        value = data.get("qr_data_url")
        if not isinstance(value, str) or not value.startswith("data:image/png;base64,"):
            raise WhatsAppDeliveryError("QR-код WhatsApp Web ещё не готов")
        return value

    def web_pairing_code(self, phone_number: str) -> dict[str, Any]:
        recipient = re.sub(r"\D", "", phone_number)
        if not 8 <= len(recipient) <= 15:
            raise WhatsAppConfigurationError(
                "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
            )
        data = self._web_request(
            "POST", "/pairing-code", payload={"phone_number": recipient}
        )
        code = data.get("pairing_code")
        expires = data.get("expires_in_seconds")
        if not isinstance(code, str) or not code:
            raise WhatsAppDeliveryError(
                "WhatsApp Web gateway не вернул код привязки"
            )
        return {
            "pairing_code": code,
            "expires_in_seconds": int(expires or 180),
        }

    def web_cancel_pairing_code(self) -> dict[str, Any]:
        return self._web_request("POST", "/pairing-code/cancel")

    def web_disconnect(self) -> dict[str, Any]:
        return self._web_request("POST", "/disconnect")

    def check_health(self) -> dict[str, str | bool | int | None]:
        if not self.configured:
            raise WhatsAppConfigurationError("WhatsApp не настроен")
        if self.is_web:
            data = self.web_status()
            return {
                "transport": "web",
                "state": str(data.get("state") or "unknown"),
                "ready": bool(data.get("ready")),
                "account": str(data["account"]) if data.get("account") else None,
                "qr_available": bool(data.get("qr_available")),
                "pairing_code_available": bool(
                    data.get("pairing_code_available")
                ),
                "pending_events": int(data.get("pending_events") or 0),
            }
        try:
            with httpx.Client(
                timeout=self.settings.whatsapp_timeout_s,
                transport=self.transport,
            ) as client:
                response = client.get(
                    self._phone_url,
                    headers=self._headers,
                    params={"fields": "display_phone_number,verified_name"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WhatsAppDeliveryError(
                "WhatsApp Cloud API не подтвердил подключение"
            ) from exc
        return {
            "transport": "cloud_api",
            "display_phone_number": data.get("display_phone_number"),
            "verified_name": data.get("verified_name"),
        }

    def send_text(self, *, to_number: str, body: str) -> str:
        if not self.configured:
            raise WhatsAppConfigurationError("WhatsApp не настроен")
        recipient = re.sub(r"\D", "", to_number)
        if not 8 <= len(recipient) <= 15:
            raise WhatsAppConfigurationError(
                "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
            )
        text = body.strip()
        if not text:
            raise WhatsAppConfigurationError("Текст сообщения пуст")
        if self.is_web:
            data = self._web_request(
                "POST", "/messages", payload={"to": recipient, "body": text[:4096]}
            )
            message_id = data.get("message_id")
            if not message_id:
                raise WhatsAppDeliveryError(
                    "WhatsApp Web gateway не подтвердил отправку сообщения"
                )
            return str(message_id)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        }
        try:
            with httpx.Client(
                timeout=self.settings.whatsapp_timeout_s,
                transport=self.transport,
            ) as client:
                response = client.post(
                    f"{self._phone_url}/messages",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            message_id = data["messages"][0]["id"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppDeliveryError(
                "WhatsApp Cloud API не отправил сообщение. Проверьте токен, "
                "Phone Number ID и открытое 24-часовое окно общения."
            ) from exc
        return str(message_id)

    def send_document(
        self,
        *,
        to_number: str,
        filename: str,
        content_type: str,
        content: bytes,
        caption: str = "",
    ) -> str:
        """Отправляет один файл как документ с сохранением имени файла."""
        if not self.configured:
            raise WhatsAppConfigurationError("WhatsApp не настроен")
        recipient = re.sub(r"\D", "", to_number)
        if not 8 <= len(recipient) <= 15:
            raise WhatsAppConfigurationError(
                "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
            )
        if not content:
            raise WhatsAppConfigurationError("Файл пуст")
        safe_filename = re.sub(
            r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1]
        ).strip()[:200] or "document"
        mime = content_type.split(";", 1)[0].strip() or "application/octet-stream"
        safe_caption = caption.strip()[:1024]
        if self.is_web:
            data = self._web_request(
                "POST",
                "/media",
                payload={
                    "to": recipient,
                    "filename": safe_filename,
                    "content_type": mime,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "caption": safe_caption,
                },
            )
            message_id = data.get("message_id")
            if not message_id:
                raise WhatsAppDeliveryError(
                    "WhatsApp Web gateway не подтвердил отправку файла"
                )
            return str(message_id)

        upload_headers = {
            key: value for key, value in self._headers.items() if key != "Content-Type"
        }
        try:
            with httpx.Client(
                timeout=self.settings.whatsapp_timeout_s,
                transport=self.transport,
            ) as client:
                uploaded = client.post(
                    f"{self._phone_url}/media",
                    headers=upload_headers,
                    data={"messaging_product": "whatsapp"},
                    files={"file": (safe_filename, content, mime)},
                )
                uploaded.raise_for_status()
                media_id = uploaded.json()["id"]
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "document",
                    "document": {
                        "id": media_id,
                        "filename": safe_filename,
                    },
                }
                if safe_caption:
                    payload["document"]["caption"] = safe_caption
                sent = client.post(
                    f"{self._phone_url}/messages",
                    headers=self._headers,
                    json=payload,
                )
                sent.raise_for_status()
                message_id = sent.json()["messages"][0]["id"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppDeliveryError(
                "WhatsApp Cloud API не отправил файл. Проверьте токен, "
                "тип файла и открытое 24-часовое окно общения."
            ) from exc
        return str(message_id)
