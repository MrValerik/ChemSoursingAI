"""Пользователь системы (вход, роль RBAC).

Не путать с Manager — это контакт на стороне поставщика.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda e: [m.value for m in e]),
        default=UserRole.BUYER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    communication_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("communication_profiles.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )
