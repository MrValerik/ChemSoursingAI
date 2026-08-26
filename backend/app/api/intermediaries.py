"""Реестр посредников: аудитору — чтение, рабочим ролям — изменение."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import Intermediary, User
from app.models.enums import UserRole
from app.services.intermediaries import domain_label, normalize_domain
from app.services.search_trace import utc_now

router = APIRouter(prefix="/intermediaries", tags=["intermediaries"])

_EDITOR_ROLES = {UserRole.BUYER, UserRole.HEAD, UserRole.ADMIN}
_KINDS = {"marketplace", "catalog", "reseller", "reference"}


class IntermediaryCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    kind: str = Field(default="marketplace", max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str) -> str:
        domain = normalize_domain(value)
        if "." not in domain:
            raise ValueError("Ожидается доменное имя, например echemi.com")
        return domain

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"Допустимые виды: {', '.join(sorted(_KINDS))}")
        return value


class IntermediaryUpdate(BaseModel):
    domain: str | None = Field(default=None, min_length=3, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        domain = normalize_domain(value)
        if "." not in domain:
            raise ValueError("Ожидается доменное имя, например echemi.com")
        return domain

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in _KINDS:
            raise ValueError(f"Допустимые виды: {', '.join(sorted(_KINDS))}")
        return value


class IntermediaryRead(BaseModel):
    id: int
    domain: str
    name: str
    kind: str
    notes: str | None
    is_active: bool
    reason: str | None = None
    source_url: str | None = None
    source_rfq_id: int | None = None
    added_by_id: int | None = None
    added_by_name: str | None = None
    created_at: datetime | None = None
    deactivated_at: datetime | None = None
    deactivated_by_name: str | None = None

    model_config = {"from_attributes": True}


def _read(item: Intermediary) -> IntermediaryRead:
    """Запись реестра вместе с тем, кто и почему её завёл."""
    data = IntermediaryRead.model_validate(item)
    data.added_by_name = item.added_by.full_name if item.added_by else None
    data.deactivated_by_name = (
        item.deactivated_by.full_name if item.deactivated_by else None
    )
    return data


def _require_editor(user: User) -> None:
    if user.role not in _EDITOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Аудитор может только просматривать реестр посредников",
        )


@router.get("", response_model=list[IntermediaryRead])
def list_intermediaries(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Intermediary]:
    return [
        _read(item)
        for item in db.scalars(
            select(Intermediary).order_by(Intermediary.kind, Intermediary.domain)
        ).all()
    ]


@router.post("", response_model=IntermediaryRead, status_code=201)
def create_intermediary(
    data: IntermediaryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Intermediary:
    _require_editor(user)
    existing = db.scalar(
        select(Intermediary).where(Intermediary.domain == data.domain)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Домен {data.domain} уже в реестре"
        )
    item = Intermediary(**data.model_dump(), added_by_id=user.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _read(item)


@router.patch("/{intermediary_id}", response_model=IntermediaryRead)
def update_intermediary(
    intermediary_id: int,
    data: IntermediaryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Intermediary:
    _require_editor(user)
    item = db.get(Intermediary, intermediary_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if data.domain is not None and data.domain != item.domain:
        duplicate = db.scalar(
            select(Intermediary).where(Intermediary.domain == data.domain)
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail=f"Домен {data.domain} уже в реестре"
            )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return _read(item)


@router.delete("/{intermediary_id}", status_code=204, response_class=Response)
def delete_intermediary(
    intermediary_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    _require_editor(user)
    item = db.get(Intermediary, intermediary_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    # Запись отключается, а не стирается. Прошлые поиски шли с этим правилом:
    # отсев по домену уже повлиял на выдачу и на решения закупщика, и убрать
    # правило задним числом значит соврать в аудите. Отключённая запись
    # перестаёт влиять на будущие поиски — этого и добивались.
    item.is_active = False
    item.deactivated_by_id = user.id
    item.deactivated_at = utc_now()
    db.commit()
    return Response(status_code=204)


class IntermediaryMark(BaseModel):
    """Отметка посредника прямо из карточки результата поиска."""

    url: str = Field(..., min_length=4, max_length=1000)
    name: str | None = Field(default=None, max_length=255)
    # Причина обязательна: правило меняет будущие поиски всех закупщиков, и
    # без причины его нельзя ни проверить, ни оспорить.
    reason: str = Field(..., min_length=3, max_length=2000)
    kind: str = Field(default="reseller", max_length=32)
    rfq_id: int | None = Field(default=None, ge=1)

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"Допустимые виды: {', '.join(sorted(_KINDS))}")
        return value


@router.post("/mark", response_model=IntermediaryRead, status_code=201)
def mark_intermediary(
    data: IntermediaryMark,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IntermediaryRead:
    """Заносит домен результата в реестр посредников с причиной и автором.

    Экспертное решение по одному результату превращается в проверяемое
    правило будущего поиска. Повторная отметка уже известного домена не
    создаёт дубликат: она обновляет причину и возвращает запись в строй,
    если та была отключена.
    """
    _require_editor(user)
    domain = normalize_domain(data.url)
    if "." not in domain:
        raise HTTPException(
            status_code=422, detail="Из ссылки не удалось выделить домен"
        )

    item = db.scalar(select(Intermediary).where(Intermediary.domain == domain))
    if item is None:
        item = Intermediary(
            domain=domain,
            name=(data.name or "").strip() or domain_label(domain),
            kind=data.kind,
        )
        db.add(item)
    item.reason = data.reason.strip()
    item.source_url = data.url
    item.source_rfq_id = data.rfq_id
    item.added_by_id = user.id
    item.is_active = True
    item.deactivated_by_id = None
    item.deactivated_at = None
    db.commit()
    db.refresh(item)
    return _read(item)


@router.post("/{intermediary_id}/restore", response_model=IntermediaryRead)
def restore_intermediary(
    intermediary_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IntermediaryRead:
    """Отменяет ошибочную отметку, не стирая её след."""
    _require_editor(user)
    item = db.get(Intermediary, intermediary_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    item.is_active = True
    item.deactivated_by_id = None
    item.deactivated_at = None
    db.commit()
    db.refresh(item)
    return _read(item)
