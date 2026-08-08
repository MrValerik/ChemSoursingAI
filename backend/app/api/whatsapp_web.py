"""Internal callback accepted only from the isolated WhatsApp Web gateway."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.schemas.integration import WhatsAppWebEvent
from app.services.communication_test_whatsapp import (
    accept_incoming_whatsapp,
    process_incoming_whatsapp,
)

router = APIRouter(prefix="/internal/whatsapp-web", tags=["internal"])


def _authorize(authorization: str = Header(default="")) -> None:
    expected = get_settings().whatsapp_web_service_token
    supplied = authorization.removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Недействительный токен gateway")


def _process(run_id: int, message_id: str) -> None:
    with SessionLocal() as db:
        process_incoming_whatsapp(db, run_id=run_id, message_id=message_id)


@router.post("/events", status_code=202, dependencies=[Depends(_authorize)])
def receive_event(
    payload: WhatsAppWebEvent,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str | int | None]:
    state, run_id = accept_incoming_whatsapp(
        db,
        message_id=payload.message_id,
        from_number=payload.from_number,
        body=payload.body,
    )
    if state == "accepted" and run_id is not None:
        background_tasks.add_task(_process, run_id, payload.message_id)
    return {"status": state, "run_id": run_id}
