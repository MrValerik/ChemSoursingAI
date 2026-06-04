"""Эндпоинты очереди эскалаций (функция 9 ТЗ)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.escalation import Escalation
from app.models.rfq import RFQ
from app.schemas.escalation import EscalationRead

router = APIRouter(tags=["escalations"])


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
