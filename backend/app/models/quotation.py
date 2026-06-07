"""Котировка: цена, валюта, базис, MOQ, грейд, оплата, срок,
наличие CoA/TDS, флаг полноты, confidence по полям."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.manager import Manager
    from app.models.rfq import RFQ


class Quotation(Base, TimestampMixin):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(primary_key=True)

    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"), index=True)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("managers.id"))

    price: Mapped[float | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    # Базис поставки из ответа поставщика — свободная строка: поставщик может
    # ответить на любом Incoterm (CIF/FOB…), не только на запрошенных CIP/FCA/EXW.
    incoterm: Mapped[str | None] = mapped_column(String(8))
    moq: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(120))
    payment_terms: Mapped[str | None] = mapped_column(String(255))
    lead_time: Mapped[str | None] = mapped_column(String(120))

    has_coa: Mapped[bool] = mapped_column(default=False)
    has_tds: Mapped[bool] = mapped_column(default=False)

    # Контроль полноты ключевых параметров (раздел 5–7 ТЗ).
    is_complete: Mapped[bool] = mapped_column(default=False)
    # Уверенность извлечения по каждому полю: {"price": 0.95, "incoterm": 0.8, ...}
    field_confidence: Mapped[dict | None] = mapped_column(JSON, default=None)

    rfq: Mapped["RFQ"] = relationship(back_populates="quotations")
    manager: Mapped["Manager | None"] = relationship()
