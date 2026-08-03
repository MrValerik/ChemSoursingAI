"""API-представление переписки с поставщиками и связанных эскалаций."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import Channel, CommDirection, DispatchStatus


class CommunicationMessageRead(BaseModel):
    id: int
    direction: CommDirection
    channel: Channel
    subject: str | None
    body: str | None
    status: str | None
    from_address: str | None
    to_address: str | None
    attachments: list[dict] | None
    created_at: datetime


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
    recipient_status: DispatchStatus | None
    last_message_at: datetime | None
    messages: list[CommunicationMessageRead] = Field(default_factory=list)
    escalations: list[CommunicationEscalationRead] = Field(default_factory=list)


class CommunicationOverviewRead(BaseModel):
    conversations: list[SupplierConversationRead] = Field(default_factory=list)
    unassigned_escalations: list[CommunicationEscalationRead] = Field(
        default_factory=list
    )


class EmailSyncRead(BaseModel):
    fetched: int
    processed: int
    duplicates: int
    unmatched: int
    quotations_created: int
    followups_drafted: int
    followups_sent: int
    escalations_created: int
    errors: list[str] = Field(default_factory=list)
