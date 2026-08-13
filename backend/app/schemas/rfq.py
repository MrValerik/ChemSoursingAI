"""Схемы запросов/ответов для RFQ."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import RFQStatus
from app.services.cas import is_valid_cas, normalize_cas, suggest_check_digit
from app.services.search_countries import normalize_search_country

# Способ идентификации предмета закупки. Номер есть не у всего, что
# закупают: у смесей, рецептур и промышленных продуктов его нет и не
# будет, но отправить по ним RFQ вполне можно.
IdentificationMethod = Literal["cas", "analog", "spec"]

# Чем аналог может отличаться от эталона. Слово «аналог» само по себе
# означает сразу всё перечисленное, и без уточнения текст письма
# поставщику собрать нельзя.
AnalogVariation = Literal["salt", "purity", "form", "manufacturer"]


class RFQCreate(BaseModel):
    """Входные данные для создания запроса (функция 1 ТЗ)."""

    identification_method: IdentificationMethod = "cas"
    cas: str | None = Field(default=None, examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
    analog_reference: str | None = Field(default=None, max_length=255)
    analog_variations: list[AnalogVariation] = Field(default_factory=list)
    specification: str | None = Field(default=None, max_length=4000)
    confirmed_synonyms: list[str] = Field(default_factory=list, max_length=50)
    excluded_names: list[str] = Field(default_factory=list, max_length=50)
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

    @model_validator(mode="after")
    def check_identification(self) -> "RFQCreate":
        """Каждый способ идентификации требует своего минимума данных.

        Проверка перекрёстная: одного взгляда на поле мало, потому что
        обязательность CAS зависит от выбранного способа.
        """
        if self.identification_method == "cas":
            if not (self.cas or "").strip():
                raise ValueError("Укажите CAS-номер или выберите другой способ")
            cas = normalize_cas(self.cas or "")
            if not is_valid_cas(cas):
                # Контрольная цифра вычисляется, поэтому не отправляем
                # закупщика сверять номер вручную — называем верный.
                hint = suggest_check_digit(cas)
                raise ValueError(
                    f"В номере ошибка. Похоже, имелся в виду {hint}"
                    if hint
                    else "CAS не прошёл проверку формата и контрольной суммы"
                )
        elif self.identification_method == "analog":
            if not (self.analog_reference or "").strip():
                raise ValueError(
                    "Укажите вещество, на которое должен быть похож аналог"
                )
        # Запрос без номера описания не требует: «нет CAS» перестало означать
        # «молекула неизвестна». Номера нет у смесей и промышленных продуктов,
        # но название у них есть, и поиск по группе названий на нём работает.
        return self


class RFQRead(BaseModel):
    """Полное представление запроса + сгенерированный текст RFQ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    identification_method: str = "cas"
    cas: str | None
    name: str
    analog_reference: str | None = None
    analog_variations: list[str] | None = None
    specification: str | None = None
    confirmed_synonyms: list[str] | None = None
    excluded_names: list[str] | None = None
    field_sources: dict | None = None
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

    # Эффективный текст: ручной сохранённый черновик либо единый шаблон.
    rfq_subject: str | None = None
    rfq_body: str | None = None
    rfq_is_customized: bool = False
    owner_name: str | None = None


class RFQMessageDraftUpdate(BaseModel):
    """Ручная версия первого RFQ; два null возвращают единый шаблон."""

    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def validate_complete_draft(self) -> "RFQMessageDraftUpdate":
        if self.subject is None and self.body is None:
            return self
        if self.subject is None or self.body is None:
            raise ValueError("Тема и текст RFQ должны быть заполнены вместе")

        subject = self.subject.strip()
        body = self.body.strip()
        if not subject or not body:
            raise ValueError("Тема и текст RFQ не могут быть пустыми")
        self.subject = subject
        self.body = body
        return self


class RFQListItem(BaseModel):
    """Строка сводной таблицы запросов (раздел 6 UI/UX-плана)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    identification_method: str = "cas"
    cas: str | None
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
