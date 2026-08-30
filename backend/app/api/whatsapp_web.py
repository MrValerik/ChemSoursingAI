"""Internal callback accepted only from the isolated WhatsApp Web gateway."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.schemas.integration import WhatsAppWebEvent
from app.services.communication_test_whatsapp import (
    accept_incoming_whatsapp,
    process_incoming_whatsapp,
)
from app.services.whatsapp_workflow import (
    accept_business_whatsapp,
    escalate_processing_failure,
    process_business_whatsapp,
    store_unmatched_whatsapp,
)

router = APIRouter(prefix="/internal/whatsapp-web", tags=["internal"])
logger = logging.getLogger(__name__)


def _authorize(authorization: str = Header(default="")) -> None:
    expected = get_settings().whatsapp_web_service_token
    supplied = authorization.removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Недействительный токен gateway")


def _process(run_id: int, message_id: str) -> None:
    with SessionLocal() as db:
        process_incoming_whatsapp(db, run_id=run_id, message_id=message_id)


def _process_business(communication_id: int) -> None:
    with SessionLocal() as db:
        try:
            process_business_whatsapp(db, communication_id=communication_id)
        except Exception:
            # Callback уже принят и удалён из очереди gateway. Не оставляем
            # сохранённую реплику без видимого результата при сбое LLM/парсера.
            db.rollback()
            logger.exception(
                "Не удалось обработать входящую WhatsApp-коммуникацию %s",
                communication_id,
            )
            escalate_processing_failure(db, communication_id=communication_id)


@router.post("/events", status_code=202, dependencies=[Depends(_authorize)])
def receive_event(
    payload: WhatsAppWebEvent,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str | int | None]:
    attachments = [item.model_dump() for item in payload.attachments]
    business = accept_business_whatsapp(
        db,
        message_id=payload.message_id,
        from_number=payload.from_number,
        body=payload.body,
        timestamp=payload.timestamp,
        quoted_message_id=payload.quoted_message_id,
        attachments=attachments,
    )
    if business.should_process:
        background_tasks.add_task(_process_business, business.communication_id)
        return {
            "status": business.state,
            "communication_id": business.communication_id,
            "rfq_id": business.rfq_id,
        }
    if business.state in {"duplicate", "ambiguous"}:
        return {
            "status": business.state,
            "communication_id": business.communication_id,
            "rfq_id": business.rfq_id,
        }

    state, run_id = accept_incoming_whatsapp(
        db,
        message_id=payload.message_id,
        from_number=payload.from_number,
        body=payload.body,
    )
    if state == "accepted" and run_id is not None:
        background_tasks.add_task(_process, run_id, payload.message_id)
        return {"status": state, "run_id": run_id}
    if state == "duplicate":
        return {"status": state, "run_id": run_id}
    unresolved = store_unmatched_whatsapp(
        db,
        message_id=payload.message_id,
        from_number=payload.from_number,
        body=payload.body,
        timestamp=payload.timestamp,
        quoted_message_id=payload.quoted_message_id,
        attachments=attachments,
    )
    return {
        "status": unresolved.state,
        "communication_id": unresolved.communication_id,
        "rfq_id": None,
    }
