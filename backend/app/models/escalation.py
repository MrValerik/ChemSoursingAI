"""Эскалация: причина, статус, ответственный."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import EscalationReason, EscalationStatus

if TYPE_CHECKING:
    from app.models.rfq import RFQ


class Escalation(Base, TimestampMixin):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)

    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"), index=True)
    reason: Mapped[EscalationReason] = mapped_column(SAEnum(EscalationReason))
    status: Mapped[EscalationStatus] = mapped_column(
        SAEnum(EscalationStatus), default=EscalationStatus.OPEN, index=True
    )
    assignee: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)

    rfq: Mapped["RFQ"] = relationship(back_populates="escalations")
