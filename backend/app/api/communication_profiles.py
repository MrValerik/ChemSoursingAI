"""Управление профилями общения, назначениями и видимым бюджетом."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models import (
    CommunicationProfile,
    CommunicationProfileVersion,
    RFQ,
    RfqAiSetting,
    User,
)
from app.models.enums import UserRole
from app.schemas.communication_profile import (
    CommunicationProfileAssignment,
    CommunicationProfileCreate,
    CommunicationProfileRead,
    CommunicationProfileStatusRead,
    CommunicationProfileUpdate,
    CommunicationProfileVersionRead,
)
from app.services.communication_profiles import budget_status, resolve_profile

router = APIRouter(prefix="/communication-profiles", tags=["communication-profiles"])
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _require_rfq_access(user: User, rfq: RFQ | None) -> RFQ:
    if (
        rfq is None
        or rfq.deleted_at is not None
        or (user.role not in _SEE_ALL_ROLES and rfq.owner_id not in (None, user.id))
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


def _snapshot(db: Session, profile: CommunicationProfile, actor: User) -> None:
    db.add(
        CommunicationProfileVersion(
            profile_id=profile.id,
            version=profile.version,
            name=profile.name,
            description=profile.description,
            system_instructions=profile.system_instructions,
            required_fields=profile.required_fields,
            max_input_chars=profile.max_input_chars,
            max_auto_replies=profile.max_auto_replies,
            max_duration_minutes=profile.max_duration_minutes,
            max_prompt_tokens=profile.max_prompt_tokens,
            max_completion_tokens=profile.max_completion_tokens,
            max_estimated_cost_usd=profile.max_estimated_cost_usd,
            changed_by=actor.full_name,
        )
    )


def _validate_assignment_profile(
    db: Session,
    profile_id: int | None,
) -> None:
    if profile_id is None:
        return
    profile = db.get(CommunicationProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль общения не найден")
    if not profile.is_active:
        raise HTTPException(
            status_code=422,
            detail="Нельзя назначить отключённый профиль общения",
        )


@router.get("", response_model=list[CommunicationProfileRead])
def list_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CommunicationProfile]:
    return list(
        db.scalars(
            select(CommunicationProfile).order_by(CommunicationProfile.id)
        ).all()
    )


@router.post("", response_model=CommunicationProfileRead, status_code=201)
def create_profile(
    payload: CommunicationProfileCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.ADMIN)),
) -> CommunicationProfile:
    if db.scalar(
        select(CommunicationProfile.id).where(
            CommunicationProfile.slug == payload.slug
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="Профиль с таким кодом уже существует",
        )
    profile = CommunicationProfile(
        **payload.model_dump(), version=1, is_system=False, updated_by=actor.full_name
    )
    db.add(profile)
    db.flush()
    _snapshot(db, profile, actor)
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/{profile_id}", response_model=CommunicationProfileRead)
def update_profile(
    profile_id: int,
    payload: CommunicationProfileUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.ADMIN)),
) -> CommunicationProfile:
    profile = db.get(CommunicationProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль общения не найден")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return profile
    if profile.slug == "buyer" and changes.get("is_active") is False:
        raise HTTPException(
            status_code=422,
            detail="Системный профиль закупщика нельзя отключить",
        )
    for key, value in changes.items():
        setattr(profile, key, value)
    profile.version += 1
    profile.updated_by = actor.full_name
    _snapshot(db, profile, actor)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{profile_id}/versions", response_model=list[CommunicationProfileVersionRead])
def profile_versions(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CommunicationProfileVersion]:
    if db.get(CommunicationProfile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Профиль общения не найден")
    return list(
        db.scalars(
            select(CommunicationProfileVersion)
            .where(CommunicationProfileVersion.profile_id == profile_id)
            .order_by(CommunicationProfileVersion.version.desc())
        ).all()
    )


@router.patch("/assignments/users/{user_id}")
def assign_user_profile(
    user_id: int,
    payload: CommunicationProfileAssignment,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    _validate_assignment_profile(db, payload.profile_id)
    user.communication_profile_id = payload.profile_id
    db.commit()
    return {"user_id": user.id, "profile_id": user.communication_profile_id}


@router.patch("/assignments/rfq/{rfq_id}")
def assign_rfq_profile(
    rfq_id: int,
    payload: CommunicationProfileAssignment,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    _validate_assignment_profile(db, payload.profile_id)
    setting = db.get(RfqAiSetting, rfq_id)
    if setting is None:
        setting = RfqAiSetting(rfq_id=rfq_id)
        db.add(setting)
    setting.communication_profile_id = payload.profile_id
    db.commit()
    return {"rfq_id": rfq_id, "profile_id": setting.communication_profile_id}


@router.get("/status/{rfq_id}", response_model=CommunicationProfileStatusRead)
def profile_status(
    rfq_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
) -> CommunicationProfileStatusRead:
    rfq = _require_rfq_access(actor, db.get(RFQ, rfq_id))
    setting = db.get(RfqAiSetting, rfq_id)
    profile = resolve_profile(db, rfq_id=rfq_id, actor_id=actor.id)
    budget = budget_status(db, profile=profile, rfq_id=rfq.id)
    if setting and setting.communication_profile_id == profile.id:
        source = "rfq"
    elif rfq.owner_id:
        owner = db.get(User, rfq.owner_id)
        source = (
            "user"
            if owner and owner.communication_profile_id == profile.id
            else "default"
        )
    elif actor.communication_profile_id == profile.id:
        source = "user"
    else:
        source = "default"
    return CommunicationProfileStatusRead(
        profile_id=profile.id,
        profile_slug=profile.slug,
        profile_name=profile.name,
        profile_version=profile.version,
        source=source,
        budget=budget.snapshot,
        stopped=not budget.allowed,
        stop_reason=budget.stop_reason,
        explanation=budget.explanation,
    )
