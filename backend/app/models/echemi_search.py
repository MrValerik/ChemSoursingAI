from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class EchemiSearch(Base, TimestampMixin):
    __tablename__ = "echemi_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int | None] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"), index=True, default=None)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    query: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    results: Mapped[list] = mapped_column(JSON, default=list)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
