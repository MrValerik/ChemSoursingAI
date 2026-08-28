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
    manufacturer: str | None = Field(default=None, max_length=255)
    origin_country: str | None = Field(default=None, max_length=120)
    packaging: str | None = Field(default=None, max_length=255)
    price_unit: str | None = Field(default=None, max_length=32)
    quoted_quantity: str | None = Field(default=None, max_length=64)
    total_price: float | None = Field(default=None, ge=0)
    delivery_cost: float | None = Field(default=None, ge=0)
    duty_cost: float | None = Field(default=None, ge=0)
    vat_cost: float | None = Field(default=None, ge=0)
    landed_cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_hazmat: bool | None = None
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
    source_communication_id: int | None
    price: float | None
    currency: str | None
    incoterm: str | None
    moq: str | None
    grade: str | None
    payment_terms: str | None
    lead_time: str | None
    manufacturer: str | None
    origin_country: str | None
    packaging: str | None
    price_unit: str | None
    quoted_quantity: str | None
    total_price: float | None
    delivery_cost: float | None
    duty_cost: float | None
    vat_cost: float | None
    landed_cost: float | None
    cost_currency: str | None
    is_hazmat: bool | None
    has_coa: bool
    has_tds: bool
    is_complete: bool
    field_confidence: dict | None
    created_at: datetime
    updated_at: datetime


class QuotationUpdate(BaseModel):
    """Ручная корректировка сравнимых условий сохранённой котировки."""

    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    incoterm: str | None = Field(default=None, max_length=8)
    moq: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=120)
    payment_terms: str | None = Field(default=None, max_length=255)
    lead_time: str | None = Field(default=None, max_length=120)
    manufacturer: str | None = Field(default=None, max_length=255)
    origin_country: str | None = Field(default=None, max_length=120)
    packaging: str | None = Field(default=None, max_length=255)
    price_unit: str | None = Field(default=None, max_length=32)
    quoted_quantity: str | None = Field(default=None, max_length=64)
    total_price: float | None = Field(default=None, ge=0)
    delivery_cost: float | None = Field(default=None, ge=0)
    duty_cost: float | None = Field(default=None, ge=0)
    vat_cost: float | None = Field(default=None, ge=0)
    landed_cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_hazmat: bool | None = None
    has_coa: bool = False
    has_tds: bool = False

    @field_validator(
        "incoterm",
        "moq",
        "grade",
        "payment_terms",
        "lead_time",
        "manufacturer",
        "origin_country",
        "packaging",
        "price_unit",
        "quoted_quantity",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("currency", "cost_currency", mode="before")
    @classmethod
    def clean_currency(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip().upper()
        return cleaned or None


class SummaryRow(BaseModel):
    """Строка сводной сравнительной таблицы по RFQ (функция 6 ТЗ)."""

    model_config = ConfigDict(from_attributes=True)

    quotation_id: int
    quotation_ids: list[int] = Field(default_factory=list)
    quotation_count: int = 1
    supplier_id: int | None = None
    manager_id: int | None = None
    test_run_id: int | None = None
    conversation_channel: str | None = None
    supplier: str | None = None
    supplier_is_manufacturer: bool | None = None
    manager: str | None = None
    price: float | None = None
    currency: str | None = None
    incoterm: str | None = None
    moq: str | None = None
    grade: str | None = None
    payment_terms: str | None = None
    lead_time: str | None = None
    manufacturer: str | None = None
    origin_country: str | None = None
    packaging: str | None = None
    price_unit: str | None = None
    quoted_quantity: str | None = None
    total_price: float | None = None
    delivery_cost: float | None = None
    duty_cost: float | None = None
    vat_cost: float | None = None
    landed_cost: float | None = None
    cost_currency: str | None = None
    is_hazmat: bool | None = None
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
