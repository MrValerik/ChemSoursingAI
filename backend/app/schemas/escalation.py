"""Схема эскалации для выдачи в API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import EscalationReason, EscalationStatus


class EscalationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    reason: EscalationReason
    status: EscalationStatus
    assignee: str | None
    note: str | None
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
