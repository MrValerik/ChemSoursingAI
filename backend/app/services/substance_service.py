"""Бизнес-правила глобального справочника химических веществ."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rfq import RFQ
from app.models.substance import Substance
from app.schemas.substance import SubstanceCreate, SubstanceDecision, SubstanceUpdate
from app.services.cas import is_valid_cas, normalize_cas


class SubstanceConflictError(ValueError):
    """Карточку нельзя создать или изменить из-за конфликта правил."""


def _merge_names(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw_name in group:
            name = raw_name.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                merged.append(name)
    return merged


def create_substance(
    db: Session,
    data: SubstanceCreate,
    *,
    reviewer_id: int,
) -> Substance:
    cas = normalize_cas(data.cas)
    if not is_valid_cas(cas):
        raise SubstanceConflictError(
            "CAS не прошёл проверку формата и контрольной суммы"
        )
    if db.scalar(select(Substance).where(Substance.cas == cas)) is not None:
        raise SubstanceConflictError("Вещество с таким CAS уже есть в справочнике")
    synonyms = _merge_names([data.preferred_name], data.synonyms)
    excluded = [
        name
        for name in _merge_names(data.excluded_names)
        if name.casefold() not in {item.casefold() for item in synonyms}
    ]
    substance = Substance(
        cas=cas,
        preferred_name=data.preferred_name,
        synonyms=synonyms,
        excluded_names=excluded,
        notes=data.notes,
        review_status="confirmed",
        reviewed_by_id=reviewer_id,
    )
    db.add(substance)
    db.commit()
    db.refresh(substance)
    return substance


def update_substance(
    db: Session,
    substance: Substance,
    data: SubstanceUpdate,
    *,
    reviewer_id: int,
) -> Substance:
    preferred_name = data.preferred_name or substance.preferred_name
    synonyms = (
        _merge_names([preferred_name], data.synonyms)
        if data.synonyms is not None
        else _merge_names([preferred_name], list(substance.synonyms or []))
    )
    excluded_source = (
        data.excluded_names
        if data.excluded_names is not None
        else list(substance.excluded_names or [])
    )
    accepted = {name.casefold() for name in synonyms}
    substance.preferred_name = preferred_name
    substance.synonyms = synonyms
    substance.excluded_names = [
        name for name in _merge_names(excluded_source) if name.casefold() not in accepted
    ]
    if data.notes is not None:
        substance.notes = data.notes.strip() or None
    substance.review_status = "confirmed"
    substance.reviewed_by_id = reviewer_id
    db.commit()
    db.refresh(substance)
    return substance


def apply_rfq_decision(
    db: Session,
    rfq: RFQ,
    data: SubstanceDecision,
    *,
    reviewer_id: int,
) -> Substance:
    cas = normalize_cas(rfq.cas)
    substance = db.scalar(select(Substance).where(Substance.cas == cas))
    if substance is None:
        substance = Substance(
            cas=cas,
            preferred_name=data.preferred_name or rfq.name,
            synonyms=[],
            excluded_names=[],
            review_status="unreviewed",
        )
        db.add(substance)
        db.flush()

    existing_synonyms = list(substance.synonyms or [])
    existing_excluded = list(substance.excluded_names or [])
    if data.action == "confirm":
        preferred_name = (
            data.preferred_name or data.suggested_name or substance.preferred_name
        )
        synonyms = _merge_names(
            existing_synonyms,
            [rfq.name, preferred_name, data.suggested_name],
            data.synonyms,
        )
        accepted = {name.casefold() for name in synonyms}
        substance.preferred_name = preferred_name
        substance.synonyms = synonyms
        substance.excluded_names = [
            name for name in existing_excluded if name.casefold() not in accepted
        ]
        substance.review_status = "confirmed"
    else:
        preferred_name = data.preferred_name or substance.preferred_name or rfq.name
        synonyms = _merge_names(existing_synonyms, [rfq.name, preferred_name])
        accepted = {name.casefold() for name in synonyms}
        substance.preferred_name = preferred_name
        substance.synonyms = synonyms
        substance.excluded_names = [
            name
            for name in _merge_names(existing_excluded, [data.suggested_name])
            if name.casefold() not in accepted
        ]
        substance.review_status = (
            "confirmed" if data.preferred_name else "needs_review"
        )

    if data.note is not None:
        substance.notes = data.note.strip() or None
    substance.verification = data.verification or rfq.verification
    substance.reviewed_by_id = reviewer_id
    rfq.substance_id = substance.id
    db.commit()
    db.refresh(substance)
    return substance
