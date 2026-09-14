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
from app.schemas.escalation import (
    EscalationEmailReplyCreate,
    EscalationRead,
    EscalationUpdate,
)
from app.services.communication_delivery import CommunicationSendError
from app.services.email_identity import (
    approve_sender_manager,
    validate_sender_manager_approval,
)
from app.services.mailbox import send_mailbox_message

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
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
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
        .join(RFQ, RFQ.id == Escalation.rfq_id)
        .options(joinedload(Escalation.rfq).joinedload(RFQ.owner))
        .where(RFQ.deleted_at.is_(None))
        .order_by(Escalation.created_at.desc())
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(
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
    if esc is None or (esc.rfq is not None and esc.rfq.deleted_at is not None):
        raise HTTPException(status_code=404, detail="Задача ручной проверки не найдена")

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


@router.post(
    "/escalations/{escalation_id}/email-reply",
    response_model=EscalationRead,
)
def reply_to_unmatched_email(
    escalation_id: int,
    payload: EscalationEmailReplyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EscalationRead:
    """Подтверждает поставщика, отвечает новому адресу и возобновляет ИИ-контур."""

    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    esc = db.get(
        Escalation,
        escalation_id,
        options=[
            joinedload(Escalation.rfq).joinedload(RFQ.owner),
            joinedload(Escalation.communication),
        ],
    )
    if esc is None or esc.rfq is None or esc.rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Задача ручной проверки не найдена")
    if user.role == UserRole.BUYER and esc.rfq.owner_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="Чужой запрос")
    if esc.communication is None:
        raise HTTPException(
            status_code=422,
            detail="У эскалации отсутствует исходное письмо для ответа",
        )

    try:
        validate_sender_manager_approval(
            db,
            rfq=esc.rfq,
            communication=esc.communication,
            selected_manager_id=payload.manager_id,
        )
        source_subject = (esc.communication.subject or f"[RFQ-{esc.rfq.id}]").strip()
        reply_subject = (
            source_subject
            if source_subject.casefold().startswith("re:")
            else f"Re: {source_subject}"
        )
        send_mailbox_message(
            db,
            to_address=esc.communication.from_address or "",
            subject=reply_subject,
            body=payload.body,
            idempotency_key=str(payload.idempotency_key),
            reply_to_message_id=esc.communication.id,
        )
        approve_sender_manager(
            db,
            rfq=esc.rfq,
            communication=esc.communication,
            selected_manager_id=payload.manager_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationSendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    esc.assignee = esc.assignee or user.full_name
    esc.status = EscalationStatus.RESOLVED
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
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
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
