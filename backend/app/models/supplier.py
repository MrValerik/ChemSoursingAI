"""Поставщик: компания, город, тип, репутация, сертификаты."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import SupplierType

if TYPE_CHECKING:
    from app.models.manager import Manager
    from app.models.user import User


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    # Имя без регистра, разделителей и юридических хвостов. По нему одна
    # компания, найденная на своём сайте и на двух площадках, остаётся
    # одной строкой, а не тремя.
    company_key: Mapped[str | None] = mapped_column(String(255), index=True)
    # Почему связи нет: «obfuscated» — адрес на странице скрыт подменой,
    # «form» — вместо адреса форма обратной связи. Пусто, если контакт
    # найден или страница не сказала ничего.
    contact_barrier: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(120))
    # Страна, с которой компанию связали. Заполняется страной поиска, и
    # сама по себе фактом о компании не является.
    country: Mapped[str | None] = mapped_column(String(120))
    # Чем эта связь подтверждена на странице: claimed — компания прямо
    # пишет, что она там; likely — косвенно, по домену или региону;
    # not_found — страница не сказала ничего. Значение mismatch сюда не
    # попадает: там страна не записывается вовсе.
    country_status: Mapped[str | None] = mapped_column(String(16), index=True)
    # Дословная цитата со страницы, на которой держится статус.
    country_evidence: Mapped[str | None] = mapped_column(String(500))
    # Номер лицензии ICP: регистрационный идентификатор сайта в материковом
    # Китае. Проверяется закупщиком самостоятельно на beian.miit.gov.cn —
    # это факт о регистрации, а не заявление продавца о себе.
    icp_licence: Mapped[str | None] = mapped_column(String(64), index=True)
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
    # Пользователь, который именно подтвердил компанию как поставщика.
    # Машинная квалификация этого поля не заполняет.
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )

    managers: Mapped[list["Manager"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )
    verified_by: Mapped["User | None"] = relationship()
