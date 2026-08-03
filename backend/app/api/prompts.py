"""Библиотека ИИ-промптов, версии, предпросмотр и настройки RFQ."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import PromptTemplate, PromptVersion, RFQ, RfqAiSetting, User
from app.models.enums import UserRole

router = APIRouter(prefix="/prompts", tags=["prompts"])
rfq_router = APIRouter(prefix="/rfq", tags=["prompts"])

PROMPT_KINDS = {
    "extraction",
    "rfq_generation",
    "substance_identity",
    "supplier_search",
    "qualification",
    "followup",
    "supplier_communication",
}
_EDIT_ROLES = {UserRole.HEAD, UserRole.ADMIN}
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


class PromptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    description: str | None
    system_prompt: str
    version: int
    is_active: bool
    updated_by: str | None
    updated_at: datetime


class PromptVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    prompt_id: int
    version: int
    name: str
    description: str | None
    system_prompt: str
    changed_by: str | None
    created_at: datetime


class PromptCreate(BaseModel):
    kind: str
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str = Field(..., min_length=20, max_length=20000)


class PromptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, min_length=20, max_length=20000)
    is_active: bool | None = None


class PromptPreviewRequest(BaseModel):
    prompt_id: int
    input_text: str = Field(..., min_length=1, max_length=30000)
    additional_instructions: str | None = Field(default=None, max_length=4000)


class RfqAiSettingRead(BaseModel):
    rfq_id: int
    prompt_template_id: int | None
    additional_instructions: str


class RfqAiSettingUpdate(BaseModel):
    prompt_template_id: int | None = None
    additional_instructions: str = Field(default="", max_length=4000)


def _require_editor(user: User) -> None:
    if user.role not in _EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Редактирование доступно руководителю и администратору")


def _require_rfq_access(user: User, rfq: RFQ) -> None:
    if user.role not in _SEE_ALL_ROLES and rfq.owner_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="Запрос не найден")


def _snapshot(db: Session, prompt: PromptTemplate, user: User) -> None:
    db.add(
        PromptVersion(
            prompt_id=prompt.id,
            version=prompt.version,
            name=prompt.name,
            description=prompt.description,
            system_prompt=prompt.system_prompt,
            changed_by=user.full_name,
        )
    )


@router.get("", response_model=list[PromptRead])
def list_prompts(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PromptTemplate]:
    return list(db.scalars(select(PromptTemplate).order_by(PromptTemplate.kind, PromptTemplate.name)).all())


@router.post("", response_model=PromptRead, status_code=201)
def create_prompt(
    data: PromptCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PromptTemplate:
    _require_editor(user)
    if data.kind not in PROMPT_KINDS:
        raise HTTPException(status_code=422, detail="Неизвестный тип промпта")
    prompt = PromptTemplate(**data.model_dump(), updated_by=user.full_name)
    db.add(prompt)
    db.flush()
    _snapshot(db, prompt, user)
    db.commit()
    db.refresh(prompt)
    return prompt


@router.patch("/{prompt_id}", response_model=PromptRead)
def update_prompt(
    prompt_id: int,
    data: PromptUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PromptTemplate:
    _require_editor(user)
    prompt = db.get(PromptTemplate, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="Настройка ИИ-агента не найдена")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return prompt
    for key, value in changes.items():
        setattr(prompt, key, value)
    prompt.version += 1
    prompt.updated_by = user.full_name
    _snapshot(db, prompt, user)
    db.commit()
    db.refresh(prompt)
    return prompt


@router.get("/{prompt_id}/versions", response_model=list[PromptVersionRead])
def prompt_versions(
    prompt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PromptVersion]:
    if db.get(PromptTemplate, prompt_id) is None:
        raise HTTPException(status_code=404, detail="Настройка ИИ-агента не найдена")
    return list(
        db.scalars(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version.desc())
        ).all()
    )


@router.post("/preview")
def preview_prompt(
    data: PromptPreviewRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    prompt = db.get(PromptTemplate, data.prompt_id)
    if prompt is None or not prompt.is_active:
        raise HTTPException(status_code=404, detail="Активный промпт не найден")
    try:
        output = LLMClient().generate_text(
            system_prompt=prompt.system_prompt,
            user_text=data.input_text,
            additional_instructions=data.additional_instructions,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Локальная ИИ-модель недоступна. "
                "Убедитесь, что сервис модели запущен, и повторите попытку"
            ),
        ) from exc
    return {"output": output, "prompt_id": prompt.id, "version": prompt.version}


@rfq_router.get("/{rfq_id}/ai-settings", response_model=RfqAiSettingRead)
def get_rfq_ai_settings(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RfqAiSettingRead:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    _require_rfq_access(user, rfq)
    setting = db.get(RfqAiSetting, rfq_id)
    return RfqAiSettingRead(
        rfq_id=rfq_id,
        prompt_template_id=setting.prompt_template_id if setting else None,
        additional_instructions=setting.additional_instructions if setting else "",
    )


@rfq_router.put("/{rfq_id}/ai-settings", response_model=RfqAiSettingRead)
def save_rfq_ai_settings(
    rfq_id: int,
    data: RfqAiSettingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RfqAiSetting:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    _require_rfq_access(user, rfq)
    if data.prompt_template_id is not None:
        prompt = db.get(PromptTemplate, data.prompt_template_id)
        if prompt is None or not prompt.is_active or prompt.kind != "extraction":
            raise HTTPException(status_code=422, detail="Нужен активный промпт извлечения")
    setting = db.get(RfqAiSetting, rfq_id)
    if setting is None:
        setting = RfqAiSetting(rfq_id=rfq_id)
        db.add(setting)
    setting.prompt_template_id = data.prompt_template_id
    setting.additional_instructions = data.additional_instructions.strip()
    db.commit()
    db.refresh(setting)
    return setting
