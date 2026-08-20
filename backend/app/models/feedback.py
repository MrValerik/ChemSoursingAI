"""Сообщение пользователя о том, чего ему не хватает."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class FeedbackMessage(Base, TimestampMixin):
    """Свободный текст из раздела «Обратная связь».

    Ни статусов, ни назначения ответственного: это не заявки в поддержку,
    а способ узнать, чего в программе не хватает. Разбирать их будут
    глазами, а разметку заведём, когда станет ясно, какая нужна.
    """

    __tablename__ = "feedback_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Автор нужен, чтобы можно было переспросить. Если учётную запись
    # удалят, само сообщение остаётся: оно и без имени говорит о нехватке.
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    # Откуда написали: раздел, в котором пользователь был перед тем, как
    # открыть форму. «Не хватает колонки» без этого приходится угадывать.
    origin: Mapped[str | None] = mapped_column(String(255), default=None)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Состояние отдельного внутреннего уведомления владельцу продукта. Само
    # обращение уже сохранено независимо от доступности SMTP.
    email_delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_attempted"
    )
    email_message_id: Mapped[str | None] = mapped_column(String(998), default=None)
    email_delivery_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    author: Mapped["User | None"] = relationship()
