"""Раздел «Обратная связь»: что пользователю мешает или непонятно.

Не служба поддержки: ответов и сроков программа не обещает. Это способ
узнать, чего в ней не хватает, — и узнать словами самого пользователя, а
не пересказом.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User
from app.models.enums import UserRole
from app.models.feedback import FeedbackMessage

router = APIRouter(tags=["feedback"], dependencies=[Depends(get_current_user)])

# Кто видит все сообщения, а не только свои: ради них раздел и заведён.
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN}

# Длиннее одного экрана текста никто не пишет, а пустая форма отправляется
# случайным нажатием.
_MAX_LENGTH = 4000


class FeedbackCreate(BaseModel):
    text: str = Field(..., max_length=_MAX_LENGTH)
    # Раздел, из которого написали. Пользователь пишет «не хватает
    # колонки», и без этого приходится гадать, где именно.
    origin: str | None = Field(default=None, max_length=255)

    @field_validator("text", "origin")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class FeedbackRead(BaseModel):
    id: int
    text: str
    origin: str | None
    author_id: int | None
    author_name: str | None
    created_at: datetime


def _to_read(message: FeedbackMessage) -> FeedbackRead:
    return FeedbackRead(
        id=message.id,
        text=message.text,
        origin=message.origin,
        author_id=message.author_id,
        author_name=message.author.full_name if message.author else None,
        created_at=message.created_at,
    )


@router.post("/feedback", response_model=FeedbackRead, status_code=201)
def send_feedback(
    data: FeedbackCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FeedbackRead:
    if not data.text:
        raise HTTPException(status_code=400, detail="Напишите, чего не хватает")

    message = FeedbackMessage(
        author_id=user.id,
        origin=data.origin or None,
        text=data.text,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _to_read(message)


@router.get("/feedback", response_model=list[FeedbackRead])
def list_feedback(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FeedbackRead]:
    """Свои сообщения — всем, все — руководителю и администратору.

    Отправленное должно быть видно отправителю: без этого нажатие кнопки
    ничем не отличается от отправки в пустоту, и второй раз человек уже
    не напишет.
    """
    stmt = (
        select(FeedbackMessage)
        .options(joinedload(FeedbackMessage.author))
        # Номер вторым ключом: у пометки времени в базе точность до
        # секунды, и два сообщения подряд получают одну и ту же. Без
        # номера порядок таких пар не определён, и свежее уезжает вниз.
        .order_by(FeedbackMessage.created_at.desc(), FeedbackMessage.id.desc())
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(FeedbackMessage.author_id == user.id)
    return [_to_read(message) for message in db.scalars(stmt).unique().all()]
