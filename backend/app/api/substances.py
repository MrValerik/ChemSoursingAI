"""Верификация и глобальный справочник химических веществ."""
from app.api.deps import get_current_user

from fastapi import Depends, APIRouter, HTTPException, Query

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.connectors.pubchem import PubChemConnector
from app.core.db import get_db
from app.models.enums import UserRole
from app.models.purchase_decision import PurchaseHistoryEntry
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.models.substance import Substance, SubstanceRevision
from app.models.user import User
from app.schemas.quotation import PurchaseHistoryRead
from app.schemas.substance import (
    SubstanceCreate,
    SubstanceDecision,
    SubstanceHistoryRead,
    SubstanceRead,
    SubstanceResolveRequest,
    SubstanceResolveResponse,
    SubstanceUpdate,
)
from app.services.substance_resolution import resolve_substance
from app.services.quotation_service import purchase_history_read
from app.services.substance_service import (
    SubstanceConflictError,
    apply_rfq_decision,
    create_substance,
    update_substance,
)

router = APIRouter(prefix="/substances", tags=["substances"], dependencies=[Depends(get_current_user)])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _can_see_rfq(user: User, rfq: RFQ) -> bool:
    return (
        user.role in _SEE_ALL_ROLES
        or rfq.owner_id is None
        or rfq.owner_id == user.id
    )


def _ensure_editor(user: User) -> None:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")


def _to_read(db: Session, substance: Substance) -> SubstanceRead:
    item = SubstanceRead.model_validate(substance)
    item.reviewed_by_name = (
        substance.reviewed_by.full_name if substance.reviewed_by else None
    )
    item.request_count = (
        db.scalar(
            select(func.count(RFQ.id)).where(RFQ.substance_id == substance.id)
        )
        or 0
    )
    return item


@router.get("/verify")
def verify_substance(cas: str = Query(..., description="CAS-номер, напр. 50-78-2")) -> dict:
    """Проверяет вещество: контрольная сумма CAS + данные PubChem.

    Echemi на этом этапе не запрашивается (заглушка в UI).
    """
    info = PubChemConnector().verify_cas(cas)
    return info.as_dict()


@router.post("/resolve", response_model=SubstanceResolveResponse)
def resolve_by_name(data: SubstanceResolveRequest) -> SubstanceResolveResponse:
    """Опознаёт вещество по названию: правильное написание и номер CAS.

    Обратная сторона `/verify`: там известен номер и проверяется вещество,
    здесь известно только название. Именно так позиции и приходят от
    заказчика — списком названий без номеров.

    Ничего не подставляет автоматически. Возвращает кандидатов с источником
    и цитатой, а выбор делает человек: у соседних названий вроде
    «Quaternium-18» и «Silicone Quaternium-18» разница видна специалисту, а
    не алгоритму. Операция только читает внешние источники, поэтому доступна
    всем ролям, включая аудитора.
    """
    resolution = resolve_substance(data.name)
    return SubstanceResolveResponse.model_validate(resolution.as_dict())


@router.get("/price-history")
def price_history(
    cas: str = Query(..., description="CAS-номер"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Возвращает историю котировок запросов с тем же CAS."""
    stmt = (
        select(Quotation, RFQ.id.label("rfq_id"))
        .join(RFQ, RFQ.id == Quotation.rfq_id)
        .where(RFQ.cas == cas.strip(), Quotation.price.is_not(None))
        .order_by(Quotation.created_at.desc())
        .limit(20)
    )
    rows = db.execute(stmt).all()
    return [
        {
            "rfq_id": rfq_id,
            "date": quotation.created_at.date().isoformat(),
            "price": float(quotation.price),
            "currency": quotation.currency,
            "incoterm": quotation.incoterm,
            "moq": quotation.moq,
        }
        for quotation, rfq_id in rows
    ]


@router.get("", response_model=list[SubstanceRead])
def list_substances(
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[SubstanceRead]:
    """Возвращает единый справочник с экспертными правилами идентификации."""
    substances = list(
        db.scalars(
            select(Substance)
            .options(joinedload(Substance.reviewed_by))
            .order_by(Substance.preferred_name)
            .limit(limit)
        ).all()
    )
    if q and q.strip():
        needle = q.strip().casefold()
        substances = [
            substance
            for substance in substances
            if needle in substance.cas.casefold()
            or needle in substance.preferred_name.casefold()
            or any(
                needle in synonym.casefold()
                for synonym in list(substance.synonyms or [])
            )
        ]
    return [_to_read(db, substance) for substance in substances]


@router.post("", response_model=SubstanceRead, status_code=201)
def add_substance(
    data: SubstanceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubstanceRead:
    _ensure_editor(user)
    try:
        substance = create_substance(db, data, reviewer_id=user.id)
    except SubstanceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_read(db, substance)


@router.post("/rfq/{rfq_id}/decision", response_model=SubstanceRead)
def decide_rfq_identity(
    rfq_id: int,
    data: SubstanceDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubstanceRead:
    """Сохраняет подтверждение или опровержение вывода ИИ для будущих запросов."""
    _ensure_editor(user)
    rfq = db.get(RFQ, rfq_id)
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see_rfq(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    substance = apply_rfq_decision(db, rfq, data, reviewer_id=user.id)
    return _to_read(db, substance)


@router.get("/{substance_id}", response_model=SubstanceRead)
def get_substance(
    substance_id: int,
    db: Session = Depends(get_db),
) -> SubstanceRead:
    substance = db.get(
        Substance,
        substance_id,
        options=[joinedload(Substance.reviewed_by)],
    )
    if substance is None:
        raise HTTPException(status_code=404, detail="Химическое вещество не найдено")
    return _to_read(db, substance)


@router.get(
    "/{substance_id}/history",
    response_model=list[SubstanceHistoryRead],
)
def get_substance_history(
    substance_id: int,
    db: Session = Depends(get_db),
) -> list[SubstanceHistoryRead]:
    """Возвращает историю экспертных решений с авторами."""
    if db.get(Substance, substance_id) is None:
        raise HTTPException(status_code=404, detail="Химическое вещество не найдено")
    revisions = list(
        db.scalars(
            select(SubstanceRevision)
            .options(joinedload(SubstanceRevision.actor))
            .where(SubstanceRevision.substance_id == substance_id)
            .order_by(SubstanceRevision.created_at.desc(), SubstanceRevision.id.desc())
        ).all()
    )
    result: list[SubstanceHistoryRead] = []
    for revision in revisions:
        item = SubstanceHistoryRead.model_validate(revision)
        item.actor_name = revision.actor.full_name if revision.actor else None
        result.append(item)
    return result


@router.get(
    "/{substance_id}/purchase-history",
    response_model=list[PurchaseHistoryRead],
)
def get_substance_purchase_history(
    substance_id: int,
    db: Session = Depends(get_db),
) -> list[PurchaseHistoryRead]:
    """История итогов всех запросов, связанных с карточкой вещества."""
    if db.get(Substance, substance_id) is None:
        raise HTTPException(status_code=404, detail="Химическое вещество не найдено")
    entries = db.scalars(
        select(PurchaseHistoryEntry)
        .options(joinedload(PurchaseHistoryEntry.actor))
        .where(PurchaseHistoryEntry.substance_id == substance_id)
        .order_by(
            PurchaseHistoryEntry.created_at.desc(),
            PurchaseHistoryEntry.id.desc(),
        )
    ).all()
    return [purchase_history_read(entry) for entry in entries]


@router.patch("/{substance_id}", response_model=SubstanceRead)
def edit_substance(
    substance_id: int,
    data: SubstanceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubstanceRead:
    _ensure_editor(user)
    substance = db.get(Substance, substance_id)
    if substance is None:
        raise HTTPException(status_code=404, detail="Химическое вещество не найдено")
    substance = update_substance(db, substance, data, reviewer_id=user.id)
    return _to_read(db, substance)
