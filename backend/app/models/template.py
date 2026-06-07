"""Шаблон сообщения (раздел 14 UI/UX-плана, функция 10 ТЗ).

Виды: ответ на типовой вопрос, дозапрос недостающих данных, WhatsApp-шаблон
(бизнес-инициированные сообщения вне окна 24ч — требуют модерации Meta).
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TemplateKind(str, enum.Enum):
    REPLY = "reply"          # ответ на типовой вопрос поставщика
    FOLLOWUP = "followup"    # дозапрос недостающих данных
    WHATSAPP = "whatsapp"    # шаблон WhatsApp (модерация Meta)


class WhatsappModeration(str, enum.Enum):
    DRAFT = "draft"          # черновик, не отправлен на модерацию
    PENDING = "pending"      # на модерации Meta
    APPROVED = "approved"    # одобрен
    REJECTED = "rejected"    # отклонён


class Template(Base, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[TemplateKind] = mapped_column(SAEnum(TemplateKind), index=True)
    name: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Статус модерации — только для WhatsApp-шаблонов.
    moderation: Mapped[WhatsappModeration | None] = mapped_column(
        SAEnum(WhatsappModeration), default=None
    )
    updated_by: Mapped[str | None] = mapped_column(String(255))
