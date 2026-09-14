"""Маршруты аутентификации: вход и текущий пользователь."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.security import create_access_token, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/guest", response_model=TokenResponse)
def guest_login() -> TokenResponse:
    from app.core.config import get_settings
    from app.core.guest import GUEST_USERNAME
    from app.models.enums import UserRole

    if not get_settings().guest_access_enabled:
        raise HTTPException(status_code=403, detail="Гостевой вход отключён администратором")
    return TokenResponse(
        access_token=create_access_token(subject=GUEST_USERNAME, role=UserRole.GUEST.value),
        user=UserRead(id=1, username=GUEST_USERNAME, full_name="Гость", role=UserRole.GUEST),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == payload.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    token = create_access_token(subject=user.username, role=user.role.value)
    return TokenResponse(access_token=token, user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(user)
