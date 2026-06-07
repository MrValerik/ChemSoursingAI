"""Схемы поставщиков и получателей рассылки (разделы 9–10 UI/UX-плана)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Channel, DispatchStatus, SupplierType


class SupplierCreate(BaseModel):
    """Ручное добавление поставщика (раздел 9: «Добавить вручную»)."""

    company: str = Field(..., min_length=1)
    type: SupplierType | None = None
    country: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    source: str | None = "добавлен вручную"
    reputation: str | None = None


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    country: str | None
    type: SupplierType | None
    reputation: str | None
    source: str | None
    certificates: list[str] | None

    # Доступные каналы по контактам менеджеров.
    channels: list[Channel] = []


class RecipientAdd(BaseModel):
    supplier_id: int
    channel: Channel


class RecipientsSelect(BaseModel):
    items: list[RecipientAdd]


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    supplier_id: int
    channel: Channel
    status: DispatchStatus
    note: str | None
    updated_at: datetime

    supplier_company: str | None = None
