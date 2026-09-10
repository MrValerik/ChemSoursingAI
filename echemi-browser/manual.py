"""Temporary control of the active challenge; never exposes a general browser API."""
import asyncio
import json
import math
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from page_state import needs_verification

router = APIRouter()
active = {"id": None, "page": None, "waiting": False, "deadline": 0.0, "controller": False}


async def wait_for_human(page, events, timeout=600):
    event = {"status": "waiting_for_user", "mode": "manual"}
    events.append(event)
    active.update(page=page, waiting=True, deadline=time.monotonic()+timeout)
    try:
        while time.monotonic() < active["deadline"]:
            if not await needs_verification(page):
                event["status"] = "passed"
                return True
            await asyncio.sleep(1)
        event["status"] = "manual_timeout"
        return False
    finally:
        active["waiting"] = False


@router.get("/manual/{search_id}")
async def status(search_id: int):
    waiting = active["id"] == search_id and active["waiting"]
    return {"waiting": waiting, "remaining_seconds": max(0,int(active["deadline"]-time.monotonic())) if waiting else 0}


def coordinates(message):
    if not isinstance(message, dict) or message.get("type") not in {"move","down","up"}:
        raise ValueError("Invalid pointer event")
    x,y = message.get("x"),message.get("y")
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (x,y)):
        raise ValueError("Invalid coordinates")
    if not (0 <= x < 1280 and 0 <= y < 900):
        raise ValueError("Outside viewport")
    return x,y


@router.websocket("/manual/{search_id}")
async def control(ws: WebSocket, search_id: int):
    await ws.accept()
    if active["id"] != search_id or not active["waiting"] or active["controller"]:
        await ws.close(code=4409)
        return
    active["controller"] = True
    page = active["page"]
    down = False
    async def frames():
        while active["id"] == search_id and active["waiting"]:
            await ws.send_bytes(await page.screenshot(type="jpeg", quality=65))
            await asyncio.sleep(.2)
        await ws.send_json({"type":"finished"})
    async def receive():
        nonlocal down
        while active["id"] == search_id and active["waiting"]:
            raw = await ws.receive_text()
            if len(raw) > 300:
                raise ValueError("Event too large")
            message = json.loads(raw)
            x,y = coordinates(message)
            # Stop input immediately if the challenge has disappeared, before collection resumes.
            if not active["waiting"] or not await needs_verification(page):
                return
            await page.mouse.move(x,y)
            if message["type"]=="down" and not down:
                await page.mouse.down()
                down = True
            elif message["type"]=="up":
                await page.mouse.up()
                down = False
    tasks = [asyncio.create_task(frames()),asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if down and not page.is_closed():
            await page.mouse.up()
        active["controller"] = False
        try:
            await ws.close()
        except (RuntimeError,WebSocketDisconnect):
            pass
