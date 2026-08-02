"""Реестр посредников: чтение всем, изменение — руководителю и админу."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import Intermediary, User
from app.models.enums import UserRole
from app.services.intermediaries import normalize_domain

router = APIRouter(prefix="/intermediaries", tags=["intermediaries"])

_EDITOR_ROLES = {UserRole.HEAD, UserRole.ADMIN}
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
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

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

    model_config = {"from_attributes": True}


def _require_editor(user: User) -> None:
    if user.role not in _EDITOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Изменять реестр посредников может руководитель или админ",
        )


@router.get("", response_model=list[IntermediaryRead])
def list_intermediaries(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Intermediary]:
    return list(
        db.scalars(
            select(Intermediary).order_by(
                Intermediary.kind, Intermediary.domain
            )
        ).all()
    )


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
    item = Intermediary(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


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
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


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
    db.delete(item)
    db.commit()
    return Response(status_code=204)
