"""API-схемы истории Email-переписки и синхронизации."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import Channel, CommDirection


class CommunicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int | None
    manager_id: int | None
    direction: CommDirection
    channel: Channel
    subject: str | None
    body: str | None
    from_address: str | None
    to_address: str | None
    status: str | None
    thread_id: str | None
    external_id: str | None
    attachments: list[dict] | None
    created_at: datetime


class EmailSyncRead(BaseModel):
    fetched: int
    processed: int
    duplicates: int
    unmatched: int
    quotations_created: int
    followups_drafted: int
    followups_sent: int
    errors: list[str]
