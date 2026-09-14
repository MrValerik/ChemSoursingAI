from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class EchemiOutreach(Base, TimestampMixin):
    __tablename__ = "echemi_outreach"
    __table_args__ = (UniqueConstraint("rfq_id", "product_url", name="uq_echemi_outreach_target"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("echemi_searches.id", ondelete="CASCADE"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    product_url: Mapped[str] = mapped_column(String(1000))
    seller_name: Mapped[str] = mapped_column(String(500))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
