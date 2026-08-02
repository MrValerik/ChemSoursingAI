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

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


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
