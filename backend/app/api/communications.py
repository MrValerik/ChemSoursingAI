"""История переписки, ручная IMAP-синхронизация и отправка черновиков."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.email import EmailConfigurationError, EmailDeliveryError
from app.core.config import get_settings
from app.core.db import get_db
from app.models.communication import Communication
from app.models.enums import UserRole
from app.models.rfq import RFQ
from app.models.user import User
from app.schemas.communication import CommunicationRead, EmailSyncRead
from app.services.email_workflow import send_followup_draft, sync_inbox

router = APIRouter(tags=["communications"])
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _require_rfq_access(user: User, rfq: RFQ | None) -> RFQ:
    if rfq is None:
        raise HTTPException(status_code=404, detail="RFQ не найден")
    if user.role not in _SEE_ALL_ROLES and rfq.owner_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="RFQ не найден")
    return rfq


@router.get(
    "/rfq/{rfq_id}/communications",
    response_model=list[CommunicationRead],
)
def list_communications(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Communication]:
    _require_rfq_access(user, db.get(RFQ, rfq_id))
    return list(
        db.scalars(
            select(Communication)
            .where(Communication.rfq_id == rfq_id)
            .order_by(Communication.created_at, Communication.id)
        ).all()
    )


@router.post("/email/sync", response_model=EmailSyncRead)
def sync_email(
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if user.role not in {UserRole.HEAD, UserRole.ADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Синхронизация общего почтового ящика доступна руководителю и администратору",
        )
    try:
        return sync_inbox(db, limit=limit).as_dict()
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/communications/{communication_id}/send",
    response_model=CommunicationRead,
)
def send_draft(
    communication_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Communication:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    communication = db.get(Communication, communication_id)
    if communication is None or communication.rfq_id is None:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    _require_rfq_access(user, db.get(RFQ, communication.rfq_id))
    if get_settings().email_delivery_mode.strip().lower() != "live":
        raise HTTPException(
            status_code=409,
            detail="Реальная отправка выключена: установите EMAIL_DELIVERY_MODE=live",
        )
    try:
        return send_followup_draft(db, communication)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
