"""Администраторская вкладка симуляции и тестовой отправки сообщений."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.db import get_db
from app.models import CommunicationTestRun, RFQ, User
from app.models.enums import UserRole
from app.schemas.integration import (
    CommunicationTestContinue,
    CommunicationTestCreate,
    CommunicationTestEscalationReply,
    CommunicationTestRead,
)
from app.services.communication_testing import (
    CommunicationTestError,
    add_demo_document_reply,
    answer_test_escalation,
    continue_communication_test,
    list_test_runs,
    run_communication_test,
    translate_test_dialogue,
)

router = APIRouter(
    prefix="/communication-testing",
    tags=["communication-testing"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.BUYER))],
)


def _require_rfq_access(db: Session, *, rfq_id: int, user: User) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if (
        rfq is None
        or rfq.deleted_at is not None
        or (
            user.role == UserRole.BUYER
            and rfq.owner_id not in (None, user.id)
        )
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


def _require_run_access(
    db: Session,
    *,
    run_id: int,
    user: User,
) -> CommunicationTestRun:
    run = db.get(CommunicationTestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Тестовый диалог не найден")
    if user.role == UserRole.BUYER:
        if (
            run.rfq_id is None
            or run.simulation_mode != "buyer_ai"
            or run.delivery_mode != "preview"
        ):
            raise HTTPException(status_code=404, detail="Тестовый диалог не найден")
        _require_rfq_access(db, rfq_id=run.rfq_id, user=user)
    return run


@router.get("", response_model=list[CommunicationTestRead])
def history(
    limit: int = Query(default=30, ge=1, le=100),
    rfq_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> list[CommunicationTestRun]:
    if user.role == UserRole.BUYER:
        if rfq_id is None:
            raise HTTPException(
                status_code=403,
                detail="Закупщику история доступна только внутри запроса",
            )
        _require_rfq_access(db, rfq_id=rfq_id, user=user)
    runs = list_test_runs(db, limit=limit, rfq_id=rfq_id)
    if user.role == UserRole.BUYER:
        return [
            run
            for run in runs
            if run.simulation_mode == "buyer_ai" and run.delivery_mode == "preview"
        ]
    return runs


@router.post("", response_model=CommunicationTestRead, status_code=201)
def run_test(
    payload: CommunicationTestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> CommunicationTestRun:
    if user.role == UserRole.BUYER:
        if payload.rfq_id is None:
            raise HTTPException(
                status_code=403,
                detail="Закупщику тестовый диалог доступен только внутри запроса",
            )
        _require_rfq_access(db, rfq_id=payload.rfq_id, user=user)
        if payload.simulation_mode != "buyer_ai":
            raise HTTPException(
                status_code=403,
                detail="Закупщику недоступна административная симуляция поставщика",
            )
        if (
            payload.delivery_mode != "preview"
            or payload.recipient
            or payload.confirm_external_send
        ):
            raise HTTPException(
                status_code=403,
                detail="Тестовый поставщик во вкладке общения работает без внешней отправки",
            )
    try:
        return run_communication_test(db, payload=payload, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{run_id}/messages", response_model=CommunicationTestRead, status_code=201)
def continue_test(
    run_id: int,
    payload: CommunicationTestContinue,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> CommunicationTestRun:
    _require_run_access(db, run_id=run_id, user=user)
    try:
        return continue_communication_test(db, run_id=run_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/{run_id}/demo-document-reply",
    response_model=CommunicationTestRead,
    status_code=201,
)
def demo_document_reply(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> CommunicationTestRun:
    """Добавляет подготовленный ответ тестового поставщика с синтетическим PDF."""
    _require_run_access(db, run_id=run_id, user=user)
    try:
        return add_demo_document_reply(db, run_id=run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/{run_id}/escalation-reply",
    response_model=CommunicationTestRead,
    status_code=201,
)
def reply_to_escalation(
    run_id: int,
    payload: CommunicationTestEscalationReply,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> CommunicationTestRun:
    _require_run_access(db, run_id=run_id, user=user)
    try:
        return answer_test_escalation(
            db,
            run_id=run_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{run_id}/translation",
    response_model=CommunicationTestRead,
)
def translate_dialogue(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.BUYER)),
) -> CommunicationTestRun:
    _require_run_access(db, run_id=run_id, user=user)
    try:
        return translate_test_dialogue(db, run_id=run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
