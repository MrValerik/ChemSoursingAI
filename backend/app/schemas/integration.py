"""Контракты администрирования каналов и тестирования общения."""

from __future__ import annotations

from datetime import datetime
from email.utils import parseaddr
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Поле не может быть пустым")
    return cleaned


class EmailIntegrationUpdate(BaseModel):
    enabled: bool = False
    delivery_mode: Literal["demo", "live"] = "demo"
    email_from: str = ""
    email_from_name: str = "ChemSource AI"
    email_timeout_s: int = Field(default=30, ge=3, le=120)
    auto_followup_mode: Literal["off", "draft", "send"] = "draft"
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str | None = Field(default=None, max_length=2048)
    smtp_use_ssl: bool = True
    smtp_starttls: bool = False
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_user: str = ""
    imap_password: str | None = Field(default=None, max_length=2048)
    imap_use_ssl: bool = True
    imap_folder: str = "INBOX"
    clear_secrets: bool = False

    @field_validator(
        "email_from",
        "email_from_name",
        "smtp_host",
        "smtp_user",
        "imap_host",
        "imap_user",
        "imap_folder",
        mode="before",
    )
    @classmethod
    def clean_strings(cls, value: object) -> str:
        return str(value or "").strip()


class EmailIntegrationRead(BaseModel):
    channel: Literal["email"] = "email"
    enabled: bool
    configured: bool
    source: Literal["database", "environment"]
    delivery_mode: Literal["demo", "live"]
    email_from: str
    email_from_name: str
    email_timeout_s: int
    auto_followup_mode: Literal["off", "draft", "send"]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password_set: bool
    smtp_use_ssl: bool
    smtp_starttls: bool
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password_set: bool
    imap_use_ssl: bool
    imap_folder: str


class WhatsAppIntegrationUpdate(BaseModel):
    enabled: bool = False
    phone_id: str = ""
    access_token: str | None = Field(default=None, max_length=4096)
    api_base_url: str = "https://graph.facebook.com"
    api_version: str = "v23.0"
    timeout_s: int = Field(default=30, ge=3, le=120)
    clear_token: bool = False

    @field_validator("phone_id", "api_base_url", "api_version", mode="before")
    @classmethod
    def clean_strings(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        cleaned = value.rstrip("/")
        if not cleaned.startswith("https://"):
            raise ValueError("WhatsApp API должен использовать HTTPS")
        return cleaned

    @field_validator("phone_id")
    @classmethod
    def validate_phone_id(cls, value: str) -> str:
        if value and not value.isdigit():
            raise ValueError("Phone Number ID должен содержать только цифры")
        return value

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        if not value.startswith("v") or not value[1:].replace(".", "").isdigit():
            raise ValueError("Версия API должна иметь формат v23.0")
        return value


class WhatsAppIntegrationRead(BaseModel):
    channel: Literal["whatsapp"] = "whatsapp"
    enabled: bool
    configured: bool
    source: Literal["database", "environment"]
    phone_id: str
    token_set: bool
    api_base_url: str
    api_version: str
    timeout_s: int


class IntegrationConnectionRead(BaseModel):
    channel: Literal["email", "whatsapp"]
    ok: bool
    message: str
    details: dict[str, str | bool | None] = Field(default_factory=dict)


class CommunicationTestCreate(BaseModel):
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(min_length=3, max_length=320)
    customer_message: str = Field(min_length=1, max_length=8000)
    reply_language: Literal["ru", "en", "zh"] = "ru"
    additional_instructions: str = Field(default="", max_length=2000)
    delivery_mode: Literal["preview", "send"] = "preview"
    subject: str = Field(default="Тест ChemSource AI", max_length=998)
    confirm_external_send: bool = False

    @field_validator(
        "recipient",
        "customer_message",
        "additional_instructions",
        "subject",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_recipient(self) -> "CommunicationTestCreate":
        if self.channel == "email":
            parsed = parseaddr(self.recipient)[1]
            if (
                not parsed
                or "@" not in parsed
                or parsed.casefold() != self.recipient.casefold()
            ):
                raise ValueError("Укажите корректный Email получателя")
        else:
            digits = re.sub(r"\D", "", self.recipient)
            if not 8 <= len(digits) <= 15:
                raise ValueError(
                    "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
                )
        return self


class CommunicationTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    recipient_masked: str
    customer_message: str
    additional_instructions: str | None
    generated_reply: str | None
    model: str | None
    reply_language: str
    delivery_mode: str
    status: str
    provider_message_id: str | None
    error: str | None
    created_at: datetime
