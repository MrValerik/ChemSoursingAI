"""Администраторская вкладка симуляции и тестовой отправки сообщений."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.db import get_db
from app.models import CommunicationTestRun, User
from app.models.enums import UserRole
from app.schemas.integration import (
    CommunicationTestContinue,
    CommunicationTestCreate,
    CommunicationTestEscalationReply,
    CommunicationTestRead,
)
from app.services.communication_testing import (
    CommunicationTestError,
    answer_test_escalation,
    continue_communication_test,
    list_test_runs,
    run_communication_test,
    translate_test_dialogue,
)

router = APIRouter(
    prefix="/communication-testing",
    tags=["communication-testing"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


@router.get("", response_model=list[CommunicationTestRead])
def history(
    limit: int = Query(default=30, ge=1, le=100),
    rfq_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> list[CommunicationTestRun]:
    return list_test_runs(db, limit=limit, rfq_id=rfq_id)


@router.post("", response_model=CommunicationTestRead, status_code=201)
def run_test(
    payload: CommunicationTestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> CommunicationTestRun:
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
) -> CommunicationTestRun:
    try:
        return continue_communication_test(db, run_id=run_id, payload=payload)
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
) -> CommunicationTestRun:
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
) -> CommunicationTestRun:
    try:
        return translate_test_dialogue(db, run_id=run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
