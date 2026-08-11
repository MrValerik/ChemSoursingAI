"""Запрос (RFQ): CAS, наименование, чистота, применение, объём,
ценовой ориентир, базисы, каналы, статус, ответственный."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RFQStatus

if TYPE_CHECKING:
    from app.models.escalation import Escalation
    from app.models.quotation import Quotation
    from app.models.search_trace import SearchRun
    from app.models.substance import Substance
    from app.models.user import User


class RFQ(Base, TimestampMixin):
    __tablename__ = "rfqs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Способ идентификации предмета закупки: cas — точная молекула по
    # номеру, analog — «как вот это вещество», spec — назначение и
    # требования. Номер есть не у всего, что закупают: у смесей, рецептур
    # и промышленных продуктов его нет и не будет.
    identification_method: Mapped[str] = mapped_column(
        String(16), default="cas", index=True
    )

    # Входные параметры продукта. CAS необязателен — см. выше.
    cas: Mapped[str | None] = mapped_column(String(20), index=True, default=None)
    name: Mapped[str] = mapped_column(String(255))

    # Режим analog: эталонное вещество и то, чем от него можно отступить
    # (соль, чистота, форма, производитель). Без второго поля «аналог»
    # означает сразу всё перечисленное и текст письма собрать нельзя.
    analog_reference: Mapped[str | None] = mapped_column(String(255), default=None)
    analog_variations: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Режим spec: требования свободным текстом (чистота и применение —
    # отдельные поля ниже).
    specification: Mapped[str | None] = mapped_column(Text, default=None)

    # Названия, отмеченные закупщиком как подходящие, и снятые им. Без
    # CAS-номера якорем поиска служит название, а оно неуникально: у
    # бетаина и его гидрохлорида названия соседние, вещества разные.
    # Снятые названия работают отрицательным фильтром в поиске.
    confirmed_synonyms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    excluded_names: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Источник каждого поля: pubchem / ai_agent / human / catalog. Хранится
    # рядом со значением, иначе находка ИИ-агента через месяц неотличима
    # от справочных данных.
    field_sources: Mapped[dict | None] = mapped_column(JSON, default=None)
    purity: Mapped[str | None] = mapped_column(String(64))
    application: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(String(64))
    target_price: Mapped[float | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3), default="USD")

    # Базисы поставки (Incoterm) и каналы рассылки (Channel) — списки строк.
    incoterms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    channels: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    search_countries: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    supplier_target: Mapped[int] = mapped_column(Integer, default=5)

    # Ручная версия первого RFQ. Оба поля либо заполнены вместе, либо остаются
    # пустыми — тогда предпросмотр и отправка используют единый шаблон.
    rfq_subject_override: Mapped[str | None] = mapped_column(
        String(500), default=None
    )
    rfq_body_override: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[RFQStatus] = mapped_column(
        SAEnum(RFQStatus), default=RFQStatus.DRAFT, index=True
    )

    # Данные верификации вещества (снимок ответа PubChem).
    verified: Mapped[bool] = mapped_column(default=False)
    verification: Mapped[dict | None] = mapped_column(JSON, default=None)
    substance_id: Mapped[int | None] = mapped_column(
        ForeignKey("substances.id"), index=True, default=None
    )
    substance: Mapped["Substance | None"] = relationship(back_populates="rfqs")

    # Ответственный закупщик (раздел 4 UI/UX-плана: данные принадлежат
    # запросу и его ответственному; роли расширяют видимость).
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, default=None
    )
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_id])

    # Мягкое удаление сохраняет историю поиска, переписку и котировки для аудита.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    deleted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )

    quotations: Mapped[list["Quotation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    search_runs: Mapped[list["SearchRun"]] = relationship(
        back_populates="rfq"
    )
