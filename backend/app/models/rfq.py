"""Запрос (RFQ): CAS, наименование, чистота, применение, объём,
ценовой ориентир, базисы, каналы, статус."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RFQStatus

if TYPE_CHECKING:
    from app.models.escalation import Escalation
    from app.models.quotation import Quotation


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

    status: Mapped[RFQStatus] = mapped_column(
        SAEnum(RFQStatus), default=RFQStatus.DRAFT, index=True
    )

    # Данные верификации вещества (снимок ответа PubChem).
    verified: Mapped[bool] = mapped_column(default=False)
    verification: Mapped[dict | None] = mapped_column(JSON, default=None)

    quotations: Mapped[list["Quotation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
