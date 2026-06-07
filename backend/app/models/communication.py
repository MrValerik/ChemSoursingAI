"""Коммуникация: направление, канал, текст, вложения, тред,
привязка к RFQ и менеджеру."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import Channel, CommDirection

if TYPE_CHECKING:
    from app.models.manager import Manager


class Communication(Base, TimestampMixin):
    __tablename__ = "communications"

    id: Mapped[int] = mapped_column(primary_key=True)

    rfq_id: Mapped[int | None] = mapped_column(ForeignKey("rfqs.id"), index=True)
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("managers.id"), index=True
    )

    direction: Mapped[CommDirection] = mapped_column(SAEnum(CommDirection))
    channel: Mapped[Channel] = mapped_column(SAEnum(Channel))
    body: Mapped[str | None] = mapped_column(Text)

    # Сшивка по треду + идемпотентность входящих (дедупликация писем).
    thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    external_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True
    )

    # Вложения (CoA/TDS): метаданные/ссылки на объектное хранилище.
    attachments: Mapped[list[dict] | None] = mapped_column(JSON, default=None)

    manager: Mapped["Manager | None"] = relationship(
        back_populates="communications"
    )
