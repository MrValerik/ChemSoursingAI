"""Эндпоинты шаблонов (раздел 14 UI/UX-плана, функция 10 ТЗ).

Матрица доступа: использование — все роли, правка — руководитель/админ.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models import Template, User
from app.models.enums import UserRole
from app.models.template import TemplateKind, WhatsappModeration
from datetime import datetime

from pydantic import ConfigDict

router = APIRouter(prefix="/templates", tags=["templates"])

_EDIT_ROLES = (UserRole.HEAD, UserRole.ADMIN)


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: TemplateKind
    name: str
    body: str
    version: int
    moderation: WhatsappModeration | None
    updated_by: str | None
    updated_at: datetime


class TemplateCreate(BaseModel):
    kind: TemplateKind
    name: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class TemplateUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    moderation: WhatsappModeration | None = None


@router.get("", response_model=list[TemplateRead])
def list_templates(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Template]:
    stmt = select(Template).order_by(Template.kind, Template.name)
    return list(db.scalars(stmt).all())


@router.post(
    "",
    response_model=TemplateRead,
    status_code=201,
)
def create_template(
    data: TemplateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_EDIT_ROLES)),
) -> Template:
    tpl = Template(
        kind=data.kind,
        name=data.name.strip(),
        body=data.body,
        version=1,
        moderation=(
            WhatsappModeration.DRAFT if data.kind == TemplateKind.WHATSAPP else None
        ),
        updated_by=user.full_name,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.patch("/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: int,
    data: TemplateUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_EDIT_ROLES)),
) -> Template:
    tpl = db.get(Template, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    changed = False
    if data.name is not None and data.name.strip() != tpl.name:
        tpl.name = data.name.strip()
        changed = True
    if data.body is not None and data.body != tpl.body:
        tpl.body = data.body
        changed = True
        # Правка текста WhatsApp-шаблона возвращает его на модерацию.
        if tpl.kind == TemplateKind.WHATSAPP:
            tpl.moderation = WhatsappModeration.DRAFT
    if data.moderation is not None and tpl.kind == TemplateKind.WHATSAPP:
        tpl.moderation = data.moderation
    if changed:
        tpl.version += 1
        tpl.updated_by = user.full_name
    db.commit()
    db.refresh(tpl)
    return tpl
