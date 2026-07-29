"""Зашифрованные настройки внешних каналов и журнал тестов общения."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class IntegrationSetting(Base, TimestampMixin):
    """Одна зашифрованная конфигурация канала."""

    __tablename__ = "integration_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_config: Mapped[str] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )

    updated_by: Mapped["User | None"] = relationship()


class CommunicationTestRun(Base, TimestampMixin):
    """Аудит симуляции сообщения и явной тестовой отправки."""

    __tablename__ = "communication_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    recipient_masked: Mapped[str] = mapped_column(String(320))
    customer_message: Mapped[str] = mapped_column(Text)
    additional_instructions: Mapped[str | None] = mapped_column(
        Text, default=None
    )
    generated_reply: Mapped[str | None] = mapped_column(Text, default=None)
    model: Mapped[str | None] = mapped_column(String(255), default=None)
    reply_language: Mapped[str] = mapped_column(String(8), default="ru")
    delivery_mode: Mapped[str] = mapped_column(String(16), default="preview")
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    actor: Mapped["User"] = relationship()
