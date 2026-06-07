"""Менеджер (контакт поставщика): ФИО, email, WhatsApp, предлагаемые вещества."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.communication import Communication
    from app.models.supplier import Supplier


class Manager(Base, TimestampMixin):
    __tablename__ = "managers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(64), index=True)
    # Вещества/группы, которые предлагает менеджер.
    offered_substances: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    supplier: Mapped["Supplier"] = relationship(back_populates="managers")

    communications: Mapped[list["Communication"]] = relationship(
        back_populates="manager"
    )
