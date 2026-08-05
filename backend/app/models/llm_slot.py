"""Слот обращения к локальной модели, общий для всех worker-процессов."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LlmSlot(Base):
    """Одно место в очереди к модели.

    Строк ровно столько, сколько слотов у llama-server. Воркеров больше:
    значительную часть поиска занимает загрузка страниц, и на это время
    место у модели занимать незачем.
    """

    __tablename__ = "llm_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(120), default=None)
    # Аренда с истечением: процесс может умереть посреди вызова, и без
    # срока место осталось бы занятым навсегда.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
