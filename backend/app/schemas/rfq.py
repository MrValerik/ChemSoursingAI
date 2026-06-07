"""Схемы запросов/ответов для RFQ."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RFQStatus


class RFQCreate(BaseModel):
    """Входные данные для создания запроса (функция 1 ТЗ)."""

    cas: str = Field(..., examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
    incoterms: list[str] = Field(..., examples=[["CIP", "FCA", "EXW"]])
    channels: list[str] = Field(default_factory=list, examples=[["email"]])
    purity: str | None = None
    application: str | None = None
    volume: str | None = None
    target_price: float | None = None
    currency: str = "USD"


class RFQRead(BaseModel):
    """Полное представление запроса + сгенерированный текст RFQ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cas: str
    name: str
    purity: str | None
    application: str | None
    volume: str | None
    target_price: float | None
    currency: str | None
    incoterms: list[str] | None
    channels: list[str] | None
    status: RFQStatus
    verified: bool
    verification: dict | None
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime

    # Вычисляемые поля (текст RFQ под выбранные базисы) — не хранятся в БД.
    rfq_subject: str | None = None
    rfq_body: str | None = None
    owner_name: str | None = None


class RFQListItem(BaseModel):
    """Строка сводной таблицы запросов (раздел 6 UI/UX-плана)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cas: str
    name: str
    status: RFQStatus
    verified: bool
    created_at: datetime

    # Обогащение для сводной таблицы.
    owner_id: int | None = None
    owner_name: str | None = None
    n_quotations: int = 0
    n_complete: int = 0
    completeness_pct: int = 0
    has_open_escalation: bool = False
