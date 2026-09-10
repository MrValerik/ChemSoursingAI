"""Бизнес-правила глобального справочника химических веществ."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.rfq import RFQ
from app.models.substance import Substance, SubstanceRevision
from app.schemas.substance import SubstanceCreate, SubstanceDecision, SubstanceUpdate
from app.services.cas import is_valid_cas, normalize_cas


class SubstanceConflictError(ValueError):
    """Карточку нельзя создать или изменить из-за конфликта правил."""


_AUDITED_FIELDS = (
    "cas",
    "preferred_name",
    "synonyms",
    "excluded_names",
    "notes",
    "review_status",
)


def _snapshot(substance: Substance) -> dict:
    return {
        "cas": substance.cas,
        "preferred_name": substance.preferred_name,
        "synonyms": list(substance.synonyms or []),
        "excluded_names": list(substance.excluded_names or []),
        "notes": substance.notes,
        "review_status": substance.review_status,
    }


def _changes(before: dict | None, after: dict) -> dict:
    if before is None:
        return {
            field: {"before": None, "after": after.get(field)}
            for field in _AUDITED_FIELDS
            if after.get(field) not in (None, [], "")
        }
    return {
        field: {"before": before.get(field), "after": after.get(field)}
        for field in _AUDITED_FIELDS
        if before.get(field) != after.get(field)
    }


def _record_revision(
    db: Session,
    substance: Substance,
    *,
    action: str,
    actor_id: int,
    before: dict | None = None,
    source_rfq_id: int | None = None,
) -> None:
    after = _snapshot(substance)
    db.add(
        SubstanceRevision(
            substance_id=substance.id,
            action=action,
            changes=_changes(before, after),
            snapshot=after,
            actor_id=actor_id,
            source_rfq_id=source_rfq_id,
        )
    )


def find_or_create_substance_for_request(
    db: Session,
    *,
    cas: str | None,
    name: str,
    verification: dict | None,
) -> tuple[Substance | None, bool]:
    """Возвращает карточку по CAS или создаёт безопасный черновик справочника.

    Название без CAS не является устойчивым идентификатором: торговая марка,
    смесь и соседняя соль могут называться почти одинаково. Поэтому такие
    запросы не объединяются автоматически и ждут экспертного решения.
    """
    if not cas:
        return None, False

    normalized_cas = normalize_cas(cas)
    substance = db.scalar(select(Substance).where(Substance.cas == normalized_cas))
    if substance is not None:
        return substance, False

    preferred_name = name.strip()
    substance = Substance(
        cas=normalized_cas,
        preferred_name=preferred_name,
        synonyms=[preferred_name],
        excluded_names=[],
        review_status="unreviewed",
        verification=verification,
    )
    try:
        # Два пользователя могут одновременно завести первый RFQ по одному
        # CAS. Уникальность БД выбирает одну карточку, а второй запрос после
        # отката только внутренней savepoint-транзакции связывается с ней.
        with db.begin_nested():
            db.add(substance)
            db.flush()
    except IntegrityError:
        substance = db.scalar(
            select(Substance).where(Substance.cas == normalized_cas)
        )
        if substance is None:
            raise
        return substance, False
    return substance, True


def record_substance_created_from_request(
    db: Session,
    substance: Substance,
    *,
    actor_id: int,
    rfq_id: int,
) -> None:
    """Фиксирует происхождение автоматически созданной карточки вещества."""
    _record_revision(
        db,
        substance,
        action="created_from_request",
        actor_id=actor_id,
        source_rfq_id=rfq_id,
    )


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
    synonyms = _merge_names([data.preferred_name], data.synonyms)
    excluded = [
        name
        for name in _merge_names(data.excluded_names)
        if name.casefold() not in {item.casefold() for item in synonyms}
    ]
    existing = db.scalar(select(Substance).where(Substance.cas == cas))
    if existing is not None:
        if existing.review_status != "unreviewed":
            raise SubstanceConflictError("Вещество с таким CAS уже есть в справочнике")
        before = _snapshot(existing)
        existing.preferred_name = data.preferred_name
        existing.synonyms = synonyms
        existing.excluded_names = excluded
        existing.notes = data.notes
        existing.review_status = "confirmed"
        existing.reviewed_by_id = reviewer_id
        _record_revision(
            db,
            existing,
            action="catalog_confirmed",
            actor_id=reviewer_id,
            before=before,
        )
        db.commit()
        db.refresh(existing)
        return existing

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
    db.flush()
    _record_revision(
        db,
        substance,
        action="created",
        actor_id=reviewer_id,
    )
    db.commit()
    db.refresh(substance)
    return substance


def _apply_cas_correction(
    db: Session,
    substance: Substance,
    new_cas: str | None,
) -> bool:
    """Меняет номер карточки, если специалист прислал другой. True — сменился.

    Номер приходит в справочник автоматически из первого запроса, поэтому
    опечатка закупщика или прайса становится ключом карточки. Проверяем его
    здесь так же строго, как при создании: контрольная сумма плюс занятость
    номера другой карточкой.
    """
    if new_cas is None:
        return False
    cas = normalize_cas(new_cas)
    if not cas or cas == substance.cas:
        return False
    if not is_valid_cas(cas):
        raise SubstanceConflictError(
            "CAS не прошёл проверку формата и контрольной суммы"
        )
    duplicate = db.scalar(select(Substance).where(Substance.cas == cas))
    if duplicate is not None:
        raise SubstanceConflictError(
            f"CAS {cas} уже занят карточкой «{duplicate.preferred_name}». "
            "Перенесите правила в неё, а лишнюю карточку не дублируйте"
        )
    substance.cas = cas
    # Автоматическая проверка подтверждала прежний номер и к новому
    # отношения не имеет. Оставить её значит показывать доказательство от
    # другого вещества — это ровно то, что запрещают продуктовые правила.
    substance.verification = None
    return True


def update_substance(
    db: Session,
    substance: Substance,
    data: SubstanceUpdate,
    *,
    reviewer_id: int,
) -> Substance:
    before = _snapshot(substance)
    cas_changed = _apply_cas_correction(db, substance, data.cas)
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
    _record_revision(
        db,
        substance,
        # Исправление номера — не то же самое, что правка синонимов:
        # в истории оно должно читаться отдельной строкой.
        action="cas_corrected" if cas_changed else "rules_updated",
        actor_id=reviewer_id,
        before=before,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        # Тот же номер могли занять параллельно между проверкой и commit.
        db.rollback()
        raise SubstanceConflictError(
            "CAS уже занят другой карточкой справочника"
        ) from exc
    db.refresh(substance)
    return substance


def apply_rfq_decision(
    db: Session,
    rfq: RFQ,
    data: SubstanceDecision,
    *,
    reviewer_id: int,
) -> Substance:
    if not rfq.cas:
        # Карточка справочника ключуется CAS-номером, поэтому запрос по
        # аналогу или спецификации в неё пока не ложится. Явный отказ
        # лучше падения на normalize_cas(None): решение эксперта здесь
        # просто нечему приписать.
        raise SubstanceConflictError(
            "У запроса нет CAS-номера — решение по карточке вещества "
            "можно принять только для запроса с номером"
        )
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

    before = _snapshot(substance)
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
    _record_revision(
        db,
        substance,
        action=(
            "identity_confirmed"
            if data.action == "confirm"
            else "identity_rejected"
        ),
        actor_id=reviewer_id,
        before=before,
        source_rfq_id=rfq.id,
    )
    db.commit()
    db.refresh(substance)
    return substance
