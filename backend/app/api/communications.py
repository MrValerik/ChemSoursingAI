"""История общения по RFQ и безопасная синхронизация входящей почты."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.email import EmailConfigurationError, EmailDeliveryError
from app.core.db import get_db
from app.models import RFQ, User
from app.models.enums import UserRole
from app.schemas.communication import CommunicationOverviewRead, EmailSyncRead
from app.services.communication_history import list_communication_overview
from app.services.email_workflow import sync_inbox

router = APIRouter(tags=["communications"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _visible_rfq(db: Session, rfq_id: int, user: User) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    if (
        user.role not in _SEE_ALL_ROLES
        and rfq.owner_id is not None
        and rfq.owner_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


@router.get(
    "/rfq/{rfq_id}/communications",
    response_model=CommunicationOverviewRead,
)
def communication_overview(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationOverviewRead:
    _visible_rfq(db, rfq_id, user)
    return list_communication_overview(db, rfq_id)


@router.post("/communications/email/sync", response_model=EmailSyncRead)
def sync_email(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailSyncRead:
    """Явно проверяет IMAP; автоматические ответы проходят policy-gate."""
    if user.role not in {UserRole.HEAD, UserRole.ADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Проверка общей почты доступна руководителю и администратору",
        )
    try:
        summary = sync_inbox(db, limit=limit)
    except EmailConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EmailSyncRead(**summary.as_dict())
