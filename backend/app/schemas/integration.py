"""Контракты администрирования каналов и тестирования общения."""

from __future__ import annotations

from datetime import datetime
from email.utils import parseaddr
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.communication import CommunicationAttachmentRead


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
    transport: Literal["cloud_api", "web"] = "cloud_api"
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
    transport: Literal["cloud_api", "web"]
    web_gateway_available: bool
    phone_id: str
    token_set: bool
    api_base_url: str
    api_version: str
    timeout_s: int


class WhatsAppWebStatusRead(BaseModel):
    state: str
    ready: bool = False
    qr_available: bool = False
    pairing_code_available: bool = False
    pairing_code_expires_in_seconds: int = 0
    client_state: str | None = None
    loading_percent: int | None = None
    proxy_configured: bool = False
    account: str | None = None
    pending_events: int = 0
    error: str | None = None


class WhatsAppWebQrRead(BaseModel):
    qr_data_url: str


class WhatsAppWebPairingCodeCreate(BaseModel):
    phone_number: str = Field(min_length=8, max_length=32)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value: object) -> str:
        cleaned = str(value or "").strip()
        digits = re.sub(r"\D", "", cleaned)
        if not 8 <= len(digits) <= 15:
            raise ValueError(
                "Номер WhatsApp должен содержать 8–15 цифр с кодом страны"
            )
        return digits


class WhatsAppWebPairingCodeRead(BaseModel):
    pairing_code: str = Field(min_length=8, max_length=16)
    expires_in_seconds: int = Field(ge=1, le=300)


class IntegrationConnectionRead(BaseModel):
    channel: Literal["email", "whatsapp"]
    ok: bool
    message: str
    details: dict[str, str | bool | int | None] = Field(default_factory=dict)


class WhatsAppWebEvent(BaseModel):
    event: Literal["message"]
    message_id: str = Field(min_length=1, max_length=255)
    from_number: str = Field(alias="from", min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=8000)
    timestamp: int


class CommunicationTestCreate(BaseModel):
    rfq_id: int | None = Field(default=None, ge=1)
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(default="", max_length=320)
    procurement_context: str = Field(default="", max_length=8000)
    # Старое имя принимается временно, чтобы не ломать существующих клиентов.
    customer_message: str = Field(default="", max_length=8000)
    # Внешние переговоры ведутся на английском. Поле оставлено для обратной
    # совместимости, но иное значение отклоняется на границе API.
    reply_language: Literal["en"] = "en"
    additional_instructions: str = Field(default="", max_length=2000)
    simulation_mode: Literal["buyer_ai", "supplier_ai"] = "buyer_ai"
    initial_message: str = Field(default="", max_length=8000)
    delivery_mode: Literal["preview", "send"] = "preview"
    subject: str = Field(default="Request for quotation", max_length=998)
    confirm_external_send: bool = False

    @field_validator(
        "recipient",
        "procurement_context",
        "customer_message",
        "additional_instructions",
        "initial_message",
        "subject",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_scenario_and_recipient(self) -> "CommunicationTestCreate":
        if not self.procurement_context and not self.customer_message:
            raise ValueError("Укажите общую информацию о закупке или веществе")
        if self.simulation_mode == "supplier_ai":
            if self.delivery_mode != "preview":
                raise ValueError("Режим «нейросеть — поставщик» доступен только в симуляции")
            if not self.initial_message:
                raise ValueError("Напишите первое сообщение покупателя")
        elif self.initial_message and self.delivery_mode != "preview":
            raise ValueError("Готовый RFQ можно использовать только в симуляции")
        if not self.recipient:
            if self.delivery_mode == "send":
                raise ValueError("Для реальной отправки укажите получателя")
            return self
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

    @property
    def scenario_text(self) -> str:
        return self.procurement_context or self.customer_message


class CommunicationTestContinue(BaseModel):
    message: str = Field(default="", max_length=8000)
    # Старое имя сохраняется для совместимости с прежней версией интерфейса.
    supplier_message: str = Field(default="", max_length=8000)
    recipient: str = Field(default="", max_length=320)
    confirm_external_send: bool = False

    @field_validator("message", "supplier_message", "recipient", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def require_message(self) -> "CommunicationTestContinue":
        if not self.message and not self.supplier_message:
            raise ValueError("Сообщение не может быть пустым")
        return self

    @property
    def participant_message(self) -> str:
        return self.message or self.supplier_message


class CommunicationTestEscalationReply(BaseModel):
    """Ручной ответ сотрудника в остановленном тестовом диалоге."""

    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message", mode="before")
    @classmethod
    def clean_message(cls, value: object) -> str:
        return str(value or "").strip()


class CommunicationTestAttachmentRead(CommunicationAttachmentRead):
    """Вложение тестового диалога с сохранённым результатом проверки ИИ."""

    verification: dict[str, Any] | None = None


class CommunicationTestMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_role: Literal["assistant", "supplier", "buyer"]
    content: str
    translation_ru: str | None
    delivery_status: str
    provider_message_id: str | None
    attachments: list[CommunicationTestAttachmentRead] | None = None
    created_at: datetime


class CommunicationTestAssessmentRead(BaseModel):
    """Детерминированный результат разбора тестовых ответов поставщика."""

    is_complete: bool
    missing_fields: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    price: float | None = None
    currency: str | None = None
    incoterm: str | None = None
    moq: str | None = None
    has_coa: bool = False
    has_tds: bool = False


class CommunicationTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int | None
    quotation_id: int | None
    channel: str
    recipient_masked: str
    procurement_context: str
    subject: str
    customer_message: str
    additional_instructions: str | None
    generated_reply: str | None
    model: str | None
    reply_language: str
    simulation_mode: Literal["buyer_ai", "supplier_ai"]
    delivery_mode: str
    status: str
    provider_message_id: str | None
    error: str | None
    created_at: datetime
    messages: list[CommunicationTestMessageRead] = Field(default_factory=list)
    quote_assessment: CommunicationTestAssessmentRead | None = None
