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

    # Входные параметры продукта.
    cas: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255))
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
