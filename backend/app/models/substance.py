"""Глобальный справочник химических веществ и экспертных правил идентификации."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.rfq import RFQ
    from app.models.user import User


class Substance(Base, TimestampMixin):
    """Подтверждённая человеком карточка вещества, переиспользуемая в запросах."""

    __tablename__ = "substances"

    id: Mapped[int] = mapped_column(primary_key=True)
    cas: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    preferred_name: Mapped[str] = mapped_column(String(255))
    synonyms: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    review_status: Mapped[str] = mapped_column(
        String(32), default="unreviewed", index=True
    )
    verification: Mapped[dict | None] = mapped_column(JSON, default=None)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, default=None
    )

    reviewed_by: Mapped["User | None"] = relationship()
    rfqs: Mapped[list["RFQ"]] = relationship(back_populates="substance")
    revisions: Mapped[list["SubstanceRevision"]] = relationship(
        back_populates="substance",
        cascade="all, delete-orphan",
    )


class SubstanceRevision(Base):
    """Неизменяемая запись экспертного решения по карточке вещества."""

    __tablename__ = "substance_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    substance_id: Mapped[int] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40), index=True)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    substance: Mapped["Substance"] = relationship(back_populates="revisions")
    actor: Mapped["User"] = relationship()
