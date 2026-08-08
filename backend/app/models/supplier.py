"""Поставщик: компания, город, тип, репутация, сертификаты."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import SupplierType

if TYPE_CHECKING:
    from app.models.manager import Manager


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    # Имя без регистра, разделителей и юридических хвостов. По нему одна
    # компания, найденная на своём сайте и на двух площадках, остаётся
    # одной строкой, а не тремя.
    company_key: Mapped[str | None] = mapped_column(String(255), index=True)
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[SupplierType | None] = mapped_column(SAEnum(SupplierType))
    reputation: Mapped[str | None] = mapped_column(String(255))
    # Источник сорсинга: сайт компании, каталог, реестр, ручное добавление.
    source: Mapped[str | None] = mapped_column(String(255))
    # Сертификаты (GMP/ISO и пр.) — список строк.
    certificates: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Статус в реестре задаётся человеком; предварительная ИИ-квалификация
    # создаёт только кандидата и не подтверждает контрагента автоматически.
    qualification_status: Mapped[str] = mapped_column(
        String(32), default="candidate", index=True
    )
    evidence_score: Mapped[int | None] = mapped_column(Integer, default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    managers: Mapped[list["Manager"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )
