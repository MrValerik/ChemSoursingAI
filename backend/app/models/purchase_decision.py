"""Ручной выбор итогового предложения по закупочному запросу."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class PurchaseDecision(Base, TimestampMixin):
    """Фиксирует решение человека, но не создаёт заказ или обязательство."""

    __tablename__ = "purchase_decisions"
    __table_args__ = (UniqueConstraint("rfq_id", name="uq_purchase_decisions_rfq_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), index=True
    )
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="RESTRICT"), index=True
    )
    selected_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)

    quotation = relationship("Quotation")
    selected_by = relationship("User")
