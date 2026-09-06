"""Явные связи одного сообщения с несколькими закупочными позициями."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.communication import Communication


class CommunicationRfqLink(Base, TimestampMixin):
    """Связь задаётся человеком или подтверждённой групповой отправкой."""

    __tablename__ = "communication_rfq_links"

    communication_id: Mapped[int] = mapped_column(
        ForeignKey("communications.id", ondelete="CASCADE"), primary_key=True
    )
    rfq_id: Mapped[int] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), primary_key=True
    )

    communication: Mapped["Communication"] = relationship(
        back_populates="rfq_links"
    )
