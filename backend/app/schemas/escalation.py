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
