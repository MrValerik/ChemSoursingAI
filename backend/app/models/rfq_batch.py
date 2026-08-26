"""Пакет закупки: несколько независимых запросов, заведённых одним действием.

Пакет — это связь между запросами, а не запрос на несколько веществ. Каждая
позиция остаётся отдельным RFQ со своим поиском, своими поставщиками и своей
котировкой: у бетаина и мочевины нет ничего общего, кроме того, что их
закупали одним списком. Слить их в одну карточку значит потерять
идентичность вещества — ровно то, на чём держится вся проверка поставщика.

Отдельная таблица, а не только колонка `batch_id` в запросе: у пакета есть
собственные данные — кто и когда его завёл, из какого файла, и ключ
идемпотентности, по которому повторное подтверждение не создаёт второй
набор запросов. Уникальность ключа нужна кому-то в схеме, и это место — она.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.rfq import RFQ
    from app.models.user import User


class RfqBatch(Base, TimestampMixin):
    __tablename__ = "rfq_batches"
    __table_args__ = (
        # Ключ уникален в пределах закупщика, а не глобально: чужой ключ не
        # должен ни блокировать создание, ни выдавать сам факт своего
        # существования.
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_rfq_batches_owner_key"
        ),
        Index("ix_rfq_batches_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_id])

    # Ключ придумывает клиент и повторяет при повторной отправке. Повтор —
    # это не только нажатая дважды кнопка: это ещё и обрыв ответа, после
    # которого закупщик не знает, создались запросы или нет.
    idempotency_key: Mapped[str] = mapped_column(String(64))

    # Имя файла, из которого пришёл список. Показывается в сводке пакета,
    # чтобы закупщик узнал свой список среди других. Сам файл не хранится.
    source_name: Mapped[str | None] = mapped_column(String(255), default=None)

    rfqs: Mapped[list["RFQ"]] = relationship(
        back_populates="batch", order_by="RFQ.id"
    )
