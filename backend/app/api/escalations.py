"""Эндпоинты очереди «Ручной разбор» (функция 9 ТЗ, раздел 13 UI/UX-плана)."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.db import get_db
from app.models import User
from app.models.enums import EscalationReason, EscalationStatus, RFQStatus, UserRole
from app.models.escalation import Escalation
from app.models.rfq import RFQ
from app.schemas.escalation import EscalationRead, EscalationUpdate

router = APIRouter(tags=["escalations"], dependencies=[Depends(get_current_user)])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _to_read(esc: Escalation) -> EscalationRead:
    read = EscalationRead.model_validate(esc)
    if esc.rfq is not None:
        read.rfq_name = esc.rfq.name
        read.rfq_cas = esc.rfq.cas
        read.rfq_owner_name = esc.rfq.owner.full_name if esc.rfq.owner else None
    return read


@router.get("/rfq/{rfq_id}/escalations", response_model=list[EscalationRead])
def list_for_rfq(rfq_id: int, db: Session = Depends(get_db)) -> list[EscalationRead]:
    """Эскалации по конкретному RFQ."""
    if db.get(RFQ, rfq_id) is None:
        raise HTTPException(status_code=404, detail="RFQ not found")
    stmt = (
        select(Escalation)
        .options(joinedload(Escalation.rfq).joinedload(RFQ.owner))
        .where(Escalation.rfq_id == rfq_id)
        .order_by(Escalation.created_at.desc())
    )
    return [_to_read(e) for e in db.scalars(stmt).all()]


@router.get("/escalations", response_model=list[EscalationRead])
def list_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EscalationRead]:
    """Очередь ручного разбора: закупщик — кейсы своих запросов, остальные — все."""
    stmt = (
        select(Escalation)
        .options(joinedload(Escalation.rfq).joinedload(RFQ.owner))
        .order_by(Escalation.created_at.desc())
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.join(RFQ, RFQ.id == Escalation.rfq_id).where(
            (RFQ.owner_id == user.id) | (RFQ.owner_id.is_(None))
        )
    return [_to_read(e) for e in db.scalars(stmt).all()]


@router.patch("/escalations/{escalation_id}", response_model=EscalationRead)
def update_escalation(
    escalation_id: int,
    payload: EscalationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EscalationRead:
    """Назначение ответственного / смена статуса / заметка по кейсу.

    Назначение и переназначение — руководитель/админ; закупщик может взять
    кейс своего запроса в работу и закрыть его с отметкой результата.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    esc = db.get(
        Escalation,
        escalation_id,
        options=[joinedload(Escalation.rfq).joinedload(RFQ.owner)],
    )
    if esc is None:
        raise HTTPException(status_code=404, detail="Escalation not found")

    if user.role == UserRole.BUYER:
        rfq = esc.rfq
        if rfq is not None and rfq.owner_id not in (None, user.id):
            raise HTTPException(status_code=403, detail="Чужой запрос")
        # Закупщик назначает только себя.
        if payload.assignee is not None and payload.assignee != user.full_name:
            raise HTTPException(
                status_code=403, detail="Назначение других — у руководителя"
            )

    if payload.assignee is not None:
        esc.assignee = payload.assignee
        if esc.status == EscalationStatus.OPEN:
            esc.status = EscalationStatus.IN_PROGRESS
    if payload.note is not None:
        esc.note = payload.note
    if payload.status is not None:
        esc.status = payload.status

    # Если все кейсы запроса решены — возвращаем RFQ из «ручного разбора».
    if payload.status == EscalationStatus.RESOLVED and esc.rfq is not None:
        open_left = db.scalar(
            select(Escalation.id)
            .where(
                Escalation.rfq_id == esc.rfq_id,
                Escalation.id != esc.id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
            .limit(1)
        )
        if open_left is None and esc.rfq.status == RFQStatus.ESCALATED:
            esc.rfq.status = RFQStatus.COLLECTING

    db.commit()
    db.refresh(esc)
    return _to_read(esc)


class EscalateRequest(BaseModel):
    reason: EscalationReason = EscalationReason.OTHER
    note: str | None = None


@router.post(
    "/rfq/{rfq_id}/escalate", response_model=EscalationRead, status_code=201
)
def escalate_manually(
    rfq_id: int,
    payload: EscalateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EscalationRead:
    """Ручная передача запроса в разбор (кнопка в шапке карточки, раздел 7)."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(RFQ, rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail="RFQ not found")
    esc = Escalation(
        rfq_id=rfq_id,
        reason=payload.reason,
        status=EscalationStatus.OPEN,
        assignee=user.full_name,
        note=payload.note,
    )
    rfq.status = RFQStatus.ESCALATED
    db.add(esc)
    db.commit()
    db.refresh(esc)
    return _to_read(esc)
