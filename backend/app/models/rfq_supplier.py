"""Связь найденной компании с закупочным запросом до выбора в рассылку."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.rfq import RFQ
    from app.models.search_trace import SearchRun
    from app.models.supplier import Supplier


class RfqSupplierLink(Base, TimestampMixin):
    __tablename__ = "rfq_supplier_links"
    __table_args__ = (
        UniqueConstraint("rfq_id", "supplier_id", name="uq_rfq_supplier_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), index=True
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), index=True
    )
    search_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_runs.id", ondelete="SET NULL"), index=True, default=None
    )
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)

    rfq: Mapped["RFQ"] = relationship()
    supplier: Mapped["Supplier"] = relationship()
    search_run: Mapped["SearchRun | None"] = relationship()
