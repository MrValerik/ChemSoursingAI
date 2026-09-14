"""Личные настройки текущего пользователя без доступа к администрированию."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User
from app.schemas.user_preferences import UserPreferences

router = APIRouter(prefix="/settings/preferences", tags=["settings"])


@router.get("", response_model=UserPreferences)
def read_preferences(user: User = Depends(get_current_user)):
    return user


@router.put("", response_model=UserPreferences)
def update_preferences(
    payload: UserPreferences,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.auto_dispatch_after_search = payload.auto_dispatch_after_search
    db.commit()
    db.refresh(user)
    return user
