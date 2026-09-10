"""Схемы запросов/ответов для RFQ."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import RFQStatus
from app.services.cas import is_valid_cas, normalize_cas, suggest_check_digit
from app.services.incoterms import SUPPORTED_INCOTERMS, normalize_incoterms
from app.services.rfq_progress import ACTION_VERIFY, STAGE_SEARCH
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
    # Чем заменять нельзя. Единственная граница, которую подбор не выведет
    # сам: «без животного происхождения» или «только пищевой грейд» знает
    # закупщик, а страница-сравнение об этом не пишет.
    analog_constraints: str | None = Field(default=None, max_length=2000)
    specification: str | None = Field(default=None, max_length=4000)
    confirmed_synonyms: list[str] = Field(default_factory=list, max_length=50)
    excluded_names: list[str] = Field(default_factory=list, max_length=50)
    incoterms: list[str] = Field(..., examples=[list(SUPPORTED_INCOTERMS)])
    channels: list[str] = Field(default_factory=list, examples=[["email"]])
    search_countries: list[str] = Field(
        default_factory=lambda: ["Китай"],
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
    target_price_unit: str | None = Field(default=None, max_length=32)
    target_price_incoterm: str | None = Field(default=None, max_length=24)
    specialist_comment: str | None = Field(default=None, max_length=4000)

    @field_validator("target_price_unit", "target_price_incoterm", mode="before")
    @classmethod
    def clean_target_basis(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return str(value or "USD").strip().upper()

    @field_validator("incoterms")
    @classmethod
    def check_incoterms(cls, values: list[str]) -> list[str]:
        """Базис отклоняется здесь, а не при сборке письма.

        Раньше единственной проверкой был генератор RFQ: запрос успевал
        создаться, и закупщик узнавал про неподдерживаемый базис уже
        после. Проверка на входе называет доступный набор сразу.

        Свой базис сверх справочника разрешён: закупщик вписал его в
        форме руками и видит, что отправляет. Проверяется только форма
        записи — место поставки такому базису не назначается.

        Пустой список отвергает не это поле, а перекрёстная проверка ниже:
        запросу на подбор аналога базис не нужен — его выбирают, когда по
        выбранным веществам заводятся настоящие запросы.
        """
        if not values:
            return []
        return normalize_incoterms(values, allow_custom=True)

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
        # Пустой список отвергает не это поле, а перекрёстная проверка ниже:
        # запросу на подбор аналога страны не нужны — их выбирают, когда по
        # выбранным веществам заводятся настоящие запросы.
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
        # Способ "analog" своего минимума не требует: заменяемое вещество —
        # это и есть название запроса. Отдельного эталона больше нет:
        # система сама подбирает вещества-заменители, а закупщик выбирает
        # из них. Раньше здесь требовалось поле «эталон» — при подборе оно
        # всегда повторяло бы название и спрашивать его стало неоткуда.
        if self.identification_method != "analog" and not self.incoterms:
            raise ValueError("Отметьте хотя бы одно условие поставки")
        if self.identification_method != "analog" and not self.search_countries:
            # Обычному запросу страна нужна сразу: без неё поиск не знает,
            # чей рынок обходить. Запросу на подбор — нет: он поставщиков
            # не ищет, а страны спрашиваются при заведении запросов по
            # выбранным аналогам, когда уже понятно, что закупают.
            raise ValueError("Выберите хотя бы одну страну поиска")
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
    analog_constraints: str | None = None
    specification: str | None = None
    confirmed_synonyms: list[str] | None = None
    excluded_names: list[str] | None = None
    field_sources: dict | None = None
    purity: str | None
    application: str | None
    volume: str | None
    target_price: float | None
    currency: str | None
    target_price_unit: str | None = None
    target_price_incoterm: str | None = None
    specialist_comment: str | None = None
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


class RFQTranslationRead(BaseModel):
    """Русский перевод сохранённого английского RFQ для внутреннего просмотра."""

    translation_ru: str


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
    # Поставщики, которым RFQ разослан: знаменатель к числу ответов.
    n_recipients: int = 0
    has_open_escalation: bool = False

    # Ход работ: стадия конвейера и ближайшее действие закупщика. Коды
    # выводит app.services.rfq_progress, подписи живут во фронтенде.
    stage: str = STAGE_SEARCH
    next_action: str = ACTION_VERIFY
    # Найдено поиском и не исключено вручную — знаменатель для «разослать».
    n_suppliers_found: int = 0
    # Компании, приславшие хотя бы один ответ, и молчащие. Считаются по
    # компаниям, а не по котировкам: одна компания присылает несколько.
    n_suppliers_replied: int = 0
    n_silent: int = 0
    # Компании, чьё сообщение осталось без нашего ответа.
    n_awaiting_our_reply: int = 0
    # Отправки, упавшие с ошибкой канала: письмо не ушло, и это не видно
    # ни по статусу, ни по числу ответов.
    n_dispatch_errors: int = 0
    # Причины открытых эскалаций — попадают прямо в подпись действия.
    escalation_reasons: list[str] = Field(default_factory=list)

    # Даты, по которым видно ход переписки. created_at остаётся, но в
    # таблице показываются эти: дата заведения ничего не говорит о работе.
    dispatched_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    # Суток с последнего ответа поставщика, а до ответов — с рассылки.
    # Считается на сервере: клиенту не с чем сравнивать наивное время,
    # которое отдаёт SQLite.
    waiting_days: int | None = None
