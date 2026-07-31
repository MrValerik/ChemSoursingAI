"""Схемы запросов/ответов для RFQ."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import RFQStatus
from app.services.search_countries import normalize_search_country


class RFQCreate(BaseModel):
    """Входные данные для создания запроса (функция 1 ТЗ)."""

    cas: str = Field(..., examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
    incoterms: list[str] = Field(..., examples=[["CIP", "FCA", "EXW"]])
    channels: list[str] = Field(default_factory=list, examples=[["email"]])
    search_countries: list[str] = Field(
        default_factory=lambda: ["Китай"],
        min_length=1,
        max_length=3,
        examples=[["Россия", "Китай", "Индия"]],
    )
    supplier_target: int = Field(default=5, ge=1, le=20)
    substance_id: int | None = Field(default=None, ge=1)
    additional_instructions: str | None = Field(default=None, max_length=4000)
    purity: str | None = None
    application: str | None = None
    volume: str | None = None
    target_price: float | None = None
    currency: str = "USD"

    @field_validator("search_countries")
    @classmethod
    def normalize_search_countries(cls, values: list[str]) -> list[str]:
        countries: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                continue
            country = normalize_search_country(value)
            key = country.casefold()
            if key not in seen:
                seen.add(key)
                countries.append(country)
        if not countries:
            raise ValueError("Выберите хотя бы одну страну поиска")
        return countries


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
    search_countries: list[str] | None
    supplier_target: int
    status: RFQStatus
    verified: bool
    verification: dict | None
    substance_id: int | None
    substance_preferred_name: str | None = None
    substance_review_status: str | None = None
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
    search_countries: list[str] | None
    supplier_target: int
    created_at: datetime

    # Обогащение для сводной таблицы.
    owner_id: int | None = None
    owner_name: str | None = None
    n_quotations: int = 0
    n_complete: int = 0
    completeness_pct: int = 0
    has_open_escalation: bool = False
