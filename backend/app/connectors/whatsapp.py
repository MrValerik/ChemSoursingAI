"""WhatsApp Cloud API connector with bounded, explicit outbound actions."""

from __future__ import annotations

import re

import httpx

from app.core.config import Settings, get_settings


class WhatsAppConfigurationError(RuntimeError):
    """Cloud API credentials or recipient are missing or malformed."""


class WhatsAppDeliveryError(RuntimeError):
    """Meta rejected the request or the connector could not reach the API."""


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
    def configured(self) -> bool:
        return bool(
            self.settings.whatsapp_token and self.settings.whatsapp_phone_id
        )

    @property
    def _phone_url(self) -> str:
        s = self.settings
        base = s.whatsapp_api_base_url.rstrip("/")
        version = s.whatsapp_api_version.strip("/")
        return f"{base}/{version}/{s.whatsapp_phone_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

    def check_health(self) -> dict[str, str | bool | None]:
        if not self.configured:
            raise WhatsAppConfigurationError(
                "WhatsApp не настроен: укажите токен и Phone Number ID"
            )
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
            "display_phone_number": data.get("display_phone_number"),
            "verified_name": data.get("verified_name"),
        }

    def send_text(self, *, to_number: str, body: str) -> str:
        if not self.configured:
            raise WhatsAppConfigurationError(
                "WhatsApp не настроен: укажите токен и Phone Number ID"
            )
        recipient = re.sub(r"\D", "", to_number)
        if not 8 <= len(recipient) <= 15:
            raise WhatsAppConfigurationError(
                "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
            )
        text = body.strip()
        if not text:
            raise WhatsAppConfigurationError("Текст сообщения пуст")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text[:4096],
            },
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
