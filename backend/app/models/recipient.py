"""Получатель рассылки RFQ: поставщик, канал, статус доставки (раздел 10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import Channel, DispatchStatus

if TYPE_CHECKING:
    from app.models.rfq import RFQ
    from app.models.supplier import Supplier


class RfqRecipient(Base, TimestampMixin):
    __tablename__ = "rfq_recipients"
    __table_args__ = (
        UniqueConstraint("rfq_id", "supplier_id", "channel", name="uq_recipient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)

    channel: Mapped[Channel] = mapped_column(SAEnum(Channel))
    status: Mapped[DispatchStatus] = mapped_column(
        SAEnum(DispatchStatus), default=DispatchStatus.QUEUED, index=True
    )
    # Примечание к статусу: «открыто 2 ч назад», «окно 24ч закрыто», ошибка.
    note: Mapped[str | None] = mapped_column(String(255))

    rfq: Mapped["RFQ"] = relationship()
    supplier: Mapped["Supplier"] = relationship()
