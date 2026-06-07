"""Поставщик: компания, город, тип, репутация, сертификаты."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import SupplierType

if TYPE_CHECKING:
    from app.models.manager import Manager


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[SupplierType | None] = mapped_column(SAEnum(SupplierType))
    reputation: Mapped[str | None] = mapped_column(String(255))
    # Источник сорсинга: сайт компании, каталог, реестр, ручное добавление.
    source: Mapped[str | None] = mapped_column(String(255))
    # Сертификаты (GMP/ISO и пр.) — список строк.
    certificates: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    managers: Mapped[list["Manager"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )
