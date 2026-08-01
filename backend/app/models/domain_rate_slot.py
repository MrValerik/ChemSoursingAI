"""Общий на все процессы слот обращения к внешнему домену."""

from __future__ import annotations

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DomainRateSlot(Base, TimestampMixin):
    """Ближайшее время, когда домену можно отправить следующий запрос.

    Пауза между обращениями к одному хосту раньше жила в памяти процесса,
    поэтому каждый worker выдерживал её самостоятельно, и суммарная частота
    росла пропорционально числу реплик. Общий счётчик в базе делает лимит
    свойством системы, а не одного процесса.

    Время хранится как epoch в секундах: это переносимо между PostgreSQL и
    SQLite и не зависит от того, как диалект округляет часовые пояса. Все
    процессы ChemSource работают на одной машине, поэтому расхождение часов
    между ними не учитывается.
    """

    __tablename__ = "domain_rate_slots"

    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    next_allowed_at: Mapped[float] = mapped_column(Float, nullable=False)
