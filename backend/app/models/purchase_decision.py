"""Ручной выбор итогового предложения по закупочному запросу."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, Text, UniqueConstraint, func
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


class PurchaseHistoryEntry(Base):
    """Неизменяемый снимок, создаваемый явной кнопкой сохранения итога."""

    __tablename__ = "purchase_history_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), index=True
    )
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="RESTRICT"), index=True
    )
    substance_id: Mapped[int | None] = mapped_column(
        ForeignKey("substances.id", ondelete="SET NULL"), index=True, default=None
    )
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), index=True, default=None
    )
    intermediary_id: Mapped[int | None] = mapped_column(
        ForeignKey("intermediaries.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor = relationship("User")
    intermediary = relationship("Intermediary")
    quotation = relationship("Quotation")
    rfq = relationship("RFQ")
    substance = relationship("Substance")
    supplier = relationship("Supplier")
