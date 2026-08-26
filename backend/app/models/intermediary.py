"""Реестр посредников: площадок, каталогов и перекупщиков.

Запрос по CAS-номеру поднимает в выдаче торговые площадки, а не заводы:
маркетплейсы под эти номера оптимизируются, производители — нет. Замер на
стенде: из 74 найденных ссылок до оценки доходили пять, и все пять оказались
перекупщиками.

Список ведётся как данные, а не как константа в коде: закупщик пополняет его
сам, по мере того как встречает новые площадки. Он же позволяет искать
осознанно среди всех продавцов — например, когда нужно сравнить цену по
российским поставщикам, а не найти изготовителя.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Intermediary(Base, TimestampMixin):
    """Домен, который не является сайтом производителя."""

    __tablename__ = "intermediaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Домен второго уровня без схемы и www: сравнение идёт по суффиксу, чтобы
    # поддомены каталога попадали под то же правило.
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # marketplace — торговая площадка, catalog — справочник веществ,
    # reseller — конкретный перекупщик, reference — сайт не о торговле вовсе.
    kind: Mapped[str] = mapped_column(String(32), default="marketplace", index=True)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    # Отключённая запись остаётся в истории, но перестаёт влиять на поиск.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Кто и почему внёс домен. Правило отсева меняет будущие поиски всех
    # закупщиков, поэтому оно должно быть предъявимым: без автора и причины
    # запись через месяц неотличима от стартового списка, и оспорить её
    # нельзя. Заполняется при отметке из карточки результата; у записей
    # стартового списка и добавленных вручную остаётся пустым.
    added_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    # Результат поиска, по которому принято решение: ссылка на страницу и
    # запрос, в котором её увидели. Это и есть доказательство отметки.
    source_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    source_rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id", ondelete="SET NULL"), default=None, index=True
    )

    # Отмена ошибочной отметки. Запись не удаляется: прошлые поиски шли с
    # этим правилом, и вычеркнуть его задним числом значит соврать в аудите.
    deactivated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    added_by: Mapped["User | None"] = relationship(foreign_keys=[added_by_id])
    deactivated_by: Mapped["User | None"] = relationship(
        foreign_keys=[deactivated_by_id]
    )
