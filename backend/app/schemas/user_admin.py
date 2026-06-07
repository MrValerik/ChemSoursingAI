"""Схемы администрирования пользователей (раздел 14: Настройки, RBAC)."""

from pydantic import BaseModel, Field

from app.models.enums import UserRole


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    full_name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    role: UserRole = UserRole.BUYER


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6)
