"""Котировка: цена, валюта, базис, MOQ, грейд, оплата, срок,
наличие CoA/TDS, флаг полноты, confidence по полям."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, String, func
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
    # Исходное входящее письмо. Связь нужна, чтобы после безопасной проверки
    # нового адреса перепривязать не только историю, но и извлечённые условия.
    source_communication_id: Mapped[int | None] = mapped_column(
        ForeignKey("communications.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )

    price: Mapped[float | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    # Базис поставки из ответа поставщика — свободная строка: поставщик может
    # ответить на любом Incoterm (CIF/FOB…), не только на запрошенных CIP/FCA/EXW.
    incoterm: Mapped[str | None] = mapped_column(String(8))
    moq: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(120))
    payment_terms: Mapped[str | None] = mapped_column(String(255))
    lead_time: Mapped[str | None] = mapped_column(String(120))

    # Коммерческая разбивка предложения. Поля повторяют только сравнимую
    # часть рабочих калькуляций заказчика: контрагент, упаковка, объём и
    # стоимость до склада. Внутренние формулы и технические Copy-столбцы
    # исходных книг сюда намеренно не переносятся.
    manufacturer: Mapped[str | None] = mapped_column(String(255), default=None)
    origin_country: Mapped[str | None] = mapped_column(String(120), default=None)
    packaging: Mapped[str | None] = mapped_column(String(255), default=None)
    price_unit: Mapped[str | None] = mapped_column(String(32), default=None)
    quoted_quantity: Mapped[str | None] = mapped_column(String(64), default=None)
    total_price: Mapped[float | None] = mapped_column(Numeric(14, 4), default=None)
    delivery_cost: Mapped[float | None] = mapped_column(Numeric(14, 4), default=None)
    duty_cost: Mapped[float | None] = mapped_column(Numeric(14, 4), default=None)
    vat_cost: Mapped[float | None] = mapped_column(Numeric(14, 4), default=None)
    landed_cost: Mapped[float | None] = mapped_column(Numeric(14, 4), default=None)
    cost_currency: Mapped[str | None] = mapped_column(String(3), default=None)
    is_hazmat: Mapped[bool | None] = mapped_column(Boolean, default=None)

    has_coa: Mapped[bool] = mapped_column(default=False)
    has_tds: Mapped[bool] = mapped_column(default=False)

    # Контроль полноты ключевых параметров (раздел 5–7 ТЗ).
    is_complete: Mapped[bool] = mapped_column(default=False)
    # Уверенность извлечения по каждому полю: {"price": 0.95, "incoterm": 0.8, ...}
    field_confidence: Mapped[dict | None] = mapped_column(JSON, default=None)
    # Текущий источник каждого коммерческого поля. Ручная правка меняет
    # источник на human; прежнее значение остаётся в quotation_field_audits.
    field_provenance: Mapped[dict | None] = mapped_column(JSON, default=None)

    rfq: Mapped["RFQ"] = relationship(back_populates="quotations")
    manager: Mapped["Manager | None"] = relationship()


class QuotationFieldAudit(Base):
    """Неизменяемая запись ручного изменения коммерческого факта."""

    __tablename__ = "quotation_field_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    old_value: Mapped[object | None] = mapped_column(JSON, default=None)
    new_value: Mapped[object | None] = mapped_column(JSON, default=None)
    old_source: Mapped[str | None] = mapped_column(String(32), default=None)
    new_source: Mapped[str] = mapped_column(String(32), default="human")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
