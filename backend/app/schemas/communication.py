"""API-представление переписки с поставщиками и связанных эскалаций."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import Channel, CommDirection, DispatchStatus


class CommunicationAttachmentRead(BaseModel):
    """Безопасные метаданные файла сообщения без его содержимого."""

    filename: str
    content_type: str | None = None
    size: int = Field(default=0, ge=0)
    document_id: int | None = Field(default=None, gt=0)
    kind: str | None = None
    status: str = "stored"
    page_count: int | None = Field(default=None, ge=0)
    error: str | None = None


class CommunicationMessageRead(BaseModel):
    id: int
    direction: CommDirection
    channel: Channel
    subject: str | None
    body: str | None
    status: str | None
    from_address: str | None
    to_address: str | None
    attachments: list[CommunicationAttachmentRead] | None
    created_at: datetime


class CommunicationSendCreate(BaseModel):
    manager_id: int = Field(gt=0)
    channel: Channel
    body: str = Field(min_length=1, max_length=12_000)
    subject: str | None = Field(default=None, max_length=998)
    idempotency_key: UUID
    confirm_external_send: bool = False

    @field_validator("body", mode="before")
    @classmethod
    def clean_body(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("subject", mode="before")
    @classmethod
    def clean_subject(cls, value: object) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None

    @model_validator(mode="after")
    def require_confirmation(self) -> "CommunicationSendCreate":
        if not self.confirm_external_send:
            raise ValueError("Подтвердите реальную внешнюю отправку")
        return self


class CommunicationDraftSend(BaseModel):
    confirm_external_send: bool = False

    @model_validator(mode="after")
    def require_confirmation(self) -> "CommunicationDraftSend":
        if not self.confirm_external_send:
            raise ValueError("Подтвердите реальную внешнюю отправку")
        return self


class CommunicationEscalationRead(BaseModel):
    id: int
    reason: str
    status: str
    assignee: str | None
    note: str | None
    communication_id: int | None
    message_body: str | None
    created_at: datetime


class SupplierConversationRead(BaseModel):
    supplier_id: int | None
    supplier_company: str
    manager_id: int | None
    manager_name: str | None
    channel: Channel
    contact: str | None
    linked_contacts: list[str] = Field(default_factory=list)
    recipient_status: DispatchStatus | None
    data_collection_status: str = "not_started"
    missing_quote_fields: list[str] = Field(default_factory=list)
    last_message_at: datetime | None
    messages: list[CommunicationMessageRead] = Field(default_factory=list)
    escalations: list[CommunicationEscalationRead] = Field(default_factory=list)


class CommunicationOverviewRead(BaseModel):
    conversations: list[SupplierConversationRead] = Field(default_factory=list)
    unassigned_escalations: list[CommunicationEscalationRead] = Field(
        default_factory=list
    )


class CommunicationTranslationCreate(BaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("message_ids")
    @classmethod
    def validate_message_ids(cls, value: list[int]) -> list[int]:
        if any(message_id <= 0 for message_id in value):
            raise ValueError("Идентификаторы сообщений должны быть положительными")
        return list(dict.fromkeys(value))


class CommunicationMessageTranslationRead(BaseModel):
    message_id: int
    translation_ru: str


class CommunicationTranslationRead(BaseModel):
    translations: list[CommunicationMessageTranslationRead]


class EmailSyncRead(BaseModel):
    fetched: int
    processed: int
    duplicates: int
    unmatched: int
    quotations_created: int
    followups_drafted: int
    followups_sent: int
    escalations_created: int
    contacts_linked: int = 0
    errors: list[str] = Field(default_factory=list)
