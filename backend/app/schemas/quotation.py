"""Схемы котировок и сводной таблицы."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuotationCreate(BaseModel):
    """Создание котировки (обычно — результат извлечения из ответа поставщика)."""

    rfq_id: int
    manager_id: int | None = None
    price: float | None = None
    currency: str | None = None
    incoterm: str | None = None
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
    incoterm: str | None
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
    supplier_id: int | None = None
    manager_id: int | None = None
    test_run_id: int | None = None
    conversation_channel: str | None = None
    supplier: str | None = None
    manager: str | None = None
    price: float | None = None
    currency: str | None = None
    incoterm: str | None = None
    moq: str | None = None
    grade: str | None = None
    payment_terms: str | None = None
    lead_time: str | None = None
    has_coa: bool = False
    has_tds: bool = False
    is_complete: bool = False
    field_confidence: dict[str, float] | None = None
    created_at: datetime


class PurchaseDecisionUpdate(BaseModel):
    """Ручной выбор предложения для итогового решения по закупке."""

    quotation_id: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def clean_note(cls, value: object) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None


class PurchaseDecisionRead(BaseModel):
    id: int
    rfq_id: int
    quotation_id: int
    selected_by_id: int | None
    selected_by_name: str | None = None
    note: str | None
    created_at: datetime
    updated_at: datetime
