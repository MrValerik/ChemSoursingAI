"""Список пользователей — для назначения ответственных (раздел 13)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.db import get_db
from app.models import User
from app.models.enums import UserRole
from app.schemas.auth import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=list[UserRead],
    dependencies=[Depends(require_roles(UserRole.HEAD, UserRole.ADMIN))],
)
def list_users(db: Session = Depends(get_db)) -> list[User]:
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.full_name)
    return list(db.scalars(stmt).all())
