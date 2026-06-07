"""Пользователи: список для назначения и администрирование (RBAC, шаг 6)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.security import hash_password
from app.models import User
from app.models.enums import UserRole
from app.schemas.auth import UserRead
from app.schemas.user_admin import UserCreate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


class UserAdminRead(UserRead):
    is_active: bool = True


@router.get(
    "",
    response_model=list[UserAdminRead],
    dependencies=[Depends(require_roles(UserRole.HEAD, UserRole.ADMIN))],
)
def list_users(db: Session = Depends(get_db)) -> list[User]:
    """Руководителю — для назначения; админу — для управления доступами."""
    stmt = select(User).order_by(User.full_name)
    return list(db.scalars(stmt).all())


@router.post(
    "",
    response_model=UserAdminRead,
    status_code=201,
)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> User:
    if db.scalar(select(User).where(User.username == data.username.strip())):
        raise HTTPException(status_code=409, detail="Логин уже занят")
    user = User(
        username=data.username.strip(),
        full_name=data.full_name.strip(),
        role=data.role,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserAdminRead)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.id == admin.id and data.is_active is False:
        raise HTTPException(status_code=422, detail="Нельзя отключить себя")
    if user.id == admin.id and data.role is not None and data.role != UserRole.ADMIN:
        raise HTTPException(status_code=422, detail="Нельзя снять с себя роль админа")
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    db.commit()
    db.refresh(user)
    return user
