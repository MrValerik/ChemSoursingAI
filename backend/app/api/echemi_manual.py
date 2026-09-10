"""Authenticated, request-scoped relay for human verification."""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session
from websockets.exceptions import WebSocketException

from app.api.deps import get_current_user
from app.connectors.echemi import get_manual_status, manual_connection
from app.schemas.echemi_search import EchemiManualStatus
from app.core.db import get_db
from app.core.security import decode_access_token
from app.models import User
from app.models.enums import UserRole
from app.models.echemi_search import EchemiSearch
from app.services.echemi_rfq import visible_searches

router = APIRouter()


def allowed(db, search_id, user):
    row = db.scalar(visible_searches(user).where(EchemiSearch.id == search_id))
    return bool(row and row.status == "running" and user.role != UserRole.AUDITOR)


@router.get("/{search_id}/manual", response_model=EchemiManualStatus)
async def manual_status(search_id: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    if not allowed(db, search_id, user):
        return {"waiting": False, "remaining_seconds": 0}
    try:
        return await get_manual_status(search_id)
    except ValueError:
        raise HTTPException(503, "Окно проверки временно недоступно")



@router.websocket("/{search_id}/manual")
async def manual_control(ws: WebSocket, search_id: int, db: Session = Depends(get_db)):
    await ws.accept()
    tasks = []
    try:
        # Authenticate in the first frame: JWT never appears in URL/access logs.
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        payload = decode_access_token(raw) if len(raw) < 8192 else None
        if payload is None:
            await ws.close(code=4401)
            return
        user = db.scalar(select(User).where(User.username == payload.get("sub")))
        if user is None or not user.is_active or not allowed(db, search_id, user):
            await ws.close(code=4403)
            return
        db.rollback()  # Do not hold a database transaction while the person works.
        async with manual_connection(search_id) as remote:
            async def upstream():
                while True:
                    event = await ws.receive_text()
                    if len(event) > 300:
                        raise ValueError("Event too large")
                    await remote.send(event)

            async def downstream():
                async for frame in remote:
                    if isinstance(frame, bytes):
                        await ws.send_bytes(frame)
                    else:
                        await ws.send_text(frame)

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            await asyncio.wait(tasks, timeout=max(0, min(610, payload["exp"] - time.time())),
                               return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, WebSocketException, OSError, ValueError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
