"""Эндпоинты очереди эскалаций (функция 9 ТЗ)."""
from app.api.deps import get_current_user

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.core.db import get_db
from app.models import User
from app.models.enums import EscalationReason, EscalationStatus, RFQStatus, UserRole
from app.models.escalation import Escalation
from app.models.rfq import RFQ
from app.schemas.escalation import EscalationRead

router = APIRouter(tags=["escalations"], dependencies=[Depends(get_current_user)])


@router.get("/rfq/{rfq_id}/escalations", response_model=list[EscalationRead])
def list_for_rfq(rfq_id: int, db: Session = Depends(get_db)) -> list[Escalation]:
    """Эскалации по конкретному RFQ."""
    if db.get(RFQ, rfq_id) is None:
        raise HTTPException(status_code=404, detail="RFQ not found")
    stmt = select(Escalation).where(Escalation.rfq_id == rfq_id).order_by(
        Escalation.created_at.desc()
    )
    return list(db.scalars(stmt).all())


@router.get("/escalations", response_model=list[EscalationRead])
def list_open(db: Session = Depends(get_db)) -> list[Escalation]:
    """Общая очередь открытых эскалаций специалисту."""
    stmt = select(Escalation).order_by(Escalation.created_at.desc())
    return list(db.scalars(stmt).all())


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
) -> Escalation:
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
    return esc
