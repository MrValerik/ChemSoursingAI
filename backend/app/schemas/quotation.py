"""Схемы котировок и сводной таблицы."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Incoterm


class QuotationCreate(BaseModel):
    """Создание котировки (обычно — результат извлечения из ответа поставщика)."""

    rfq_id: int
    manager_id: int | None = None
    price: float | None = None
    currency: str | None = None
    incoterm: Incoterm | None = None
    moq: str | None = None
    grade: str | None = None
    payment_terms: str | None = None
    lead_time: str | None = None
    has_coa: bool = False
    has_tds: bool = False
    field_confidence: dict[str, float] | None = None
    # Свободный текст ответа — для правил эскалации (дефицит/кастом-синтез/логистика).
    source_text: str = Field(default="", exclude=True)


class QuotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    manager_id: int | None
    price: float | None
    currency: str | None
    incoterm: Incoterm | None
    moq: str | None
    grade: str | None
    payment_terms: str | None
    lead_time: str | None
    has_coa: bool
    has_tds: bool
    is_complete: bool
    field_confidence: dict | None
    created_at: datetime
    updated_at: datetime


class SummaryRow(BaseModel):
    """Строка сводной сравнительной таблицы по RFQ (функция 6 ТЗ)."""

    model_config = ConfigDict(from_attributes=True)

    quotation_id: int
    supplier: str | None = None
    manager: str | None = None
    price: float | None = None
    currency: str | None = None
    incoterm: Incoterm | None = None
    moq: str | None = None
    grade: str | None = None
    lead_time: str | None = None
    has_coa: bool = False
    has_tds: bool = False
    is_complete: bool = False
