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
    """Аудит тестового диалога и явной тестовой отправки."""

    __tablename__ = "communication_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    recipient_masked: Mapped[str] = mapped_column(String(320))
    # Реальный адрес нужен только для сопоставления входящих ответов WhatsApp.
    # Значение шифруется, а детерминированный keyed hash позволяет безопасный поиск.
    recipient_key: Mapped[str | None] = mapped_column(
        String(64), default=None, index=True
    )
    recipient_ciphertext: Mapped[str | None] = mapped_column(Text, default=None)
    procurement_context: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(
        String(998), default="Request for quotation"
    )
    # Сохраняется для совместимости со старыми журналами/API. Для новых
    # диалогов содержит последнюю реплику поставщика (до неё — контекст).
    customer_message: Mapped[str] = mapped_column(Text)
    additional_instructions: Mapped[str | None] = mapped_column(
        Text, default=None
    )
    generated_reply: Mapped[str | None] = mapped_column(Text, default=None)
    model: Mapped[str | None] = mapped_column(String(255), default=None)
    reply_language: Mapped[str] = mapped_column(String(8), default="en")
    delivery_mode: Mapped[str] = mapped_column(String(16), default="preview")
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    actor: Mapped["User"] = relationship()
    messages: Mapped[list["CommunicationTestMessage"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="CommunicationTestMessage.id",
    )


class CommunicationTestMessage(Base, TimestampMixin):
    """Оригинальная реплика участника тестового диалога."""

    __tablename__ = "communication_test_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("communication_test_runs.id", ondelete="CASCADE"), index=True
    )
    sender_role: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    # Русский перевод для внутреннего интерфейса. Оригинал остаётся неизменным
    # и только он используется в истории модели и внешней отправке.
    translation_ru: Mapped[str | None] = mapped_column(Text, default=None)
    delivery_status: Mapped[str] = mapped_column(
        String(32), default="previewed"
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), default=None, unique=True, index=True
    )

    run: Mapped["CommunicationTestRun"] = relationship(back_populates="messages")
