"""Схема эскалации для выдачи в API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import EscalationReason, EscalationStatus


class EscalationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    reason: EscalationReason
    status: EscalationStatus
    assignee: str | None
    note: str | None
    suggested_reply: str | None
    created_at: datetime

    # Сведения о запросе для очереди «Ручной разбор» (раздел 13).
    rfq_name: str | None = None
    rfq_cas: str | None = None
    rfq_owner_name: str | None = None


class EscalationUpdate(BaseModel):
    """Назначение/закрытие кейса (раздел 13: руководитель назначает)."""

    assignee: str | None = None
    status: EscalationStatus | None = None
    note: str | None = None


class EscalationEmailReplyCreate(BaseModel):
    """Подтверждённый ответ после ручного выбора поставщика."""

    manager_id: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=12_000)
    idempotency_key: UUID
    confirm_external_send: bool = False

    @field_validator("body", mode="before")
    @classmethod
    def clean_body(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def require_confirmation(self) -> "EscalationEmailReplyCreate":
        if not self.confirm_external_send:
            raise ValueError("Подтвердите реальную внешнюю отправку")
        return self
