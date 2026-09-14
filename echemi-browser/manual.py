"""Temporary control of the active challenge; never exposes a general browser API."""
import asyncio
import json
import math
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from page_state import needs_verification
from diagnostics import public_url
from manual_recording import RecordingBudget, client_time

router = APIRouter()
active = {"id": None, "page": None, "waiting": False, "deadline": 0.0, "controller": False}


async def wait_for_human(page, events, timeout=600, stage="unknown"):
    event = {"status": "waiting_for_user", "mode": "manual", "stage": stage,
             "url": public_url(getattr(page, "url", "")), "recordings": []}
    events.append(event)
    active.update(page=page, waiting=True, deadline=time.monotonic()+timeout, manual_event=event)
    if active.get("recording_budget") is None:
        active["recording_budget"] = RecordingBudget()
    try:
        while time.monotonic() < active["deadline"]:
            if not await needs_verification(page):
                event["status"] = "passed"
                return True
            await asyncio.sleep(1)
        event["status"] = "manual_timeout"
        return False
    finally:
        if event["status"] == "waiting_for_user":
            event["status"] = "interrupted"
        active["waiting"] = False
        # Release any held button and stop recording before collection resumes.
        stop, done = active.get("control_stop"), active.get("control_done")
        if stop is not None and done is not None:
            stop.set()
            await done.wait()
        active["manual_event"] = None


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
    stop, done = asyncio.Event(), asyncio.Event()
    active.update(control_stop=stop, control_done=done)
    page = active["page"]
    manual_event = active["manual_event"]
    recording = active["recording_budget"].start(manual_event)
    reason = "disconnected"
    down = False
    async def frames():
        previous = None
        while active["id"] == search_id and active["waiting"]:
            state = recording.state() if recording else {"type": "recording", "id": None,
                                                         "limited": True, "event_count": 0, "dropped_events": 0}
            if state != previous:
                await ws.send_json(state)
                previous = state
            await ws.send_bytes(await page.screenshot(type="jpeg", quality=65))
            await asyncio.sleep(.2)
        await ws.send_json({"type":"finished"})
    async def receive():
        nonlocal down, reason
        while active["id"] == search_id and active["waiting"]:
            raw = await ws.receive_text()
            received_at = time.monotonic()
            if len(raw) > 300:
                raise ValueError("Event too large")
            message = json.loads(raw)
            x,y = coordinates(message)
            t_ms = client_time(message)
            # Stop input immediately if the challenge has disappeared, before collection resumes.
            if not active["waiting"] or not await needs_verification(page):
                reason = "challenge_disappeared"
                return
            event = recording.append(message["type"], x, y, t_ms, received_at) if recording else None
            await page.mouse.move(x,y)
            if message["type"]=="down" and not down:
                await page.mouse.down()
                down = True
            elif message["type"]=="up":
                await page.mouse.up()
                down = False
            if recording:
                recording.delivered(event)
    tasks = [asyncio.create_task(frames()),asyncio.create_task(receive()),asyncio.create_task(stop.wait())]
    try:
        completed, _ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in completed:
            task.result()
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        reason = "interrupted"
        raise
    except Exception:
        reason = "connection_error"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        try:
            if down and not page.is_closed():
                await page.mouse.up()
        except Exception:
            reason = "connection_error"
        if manual_event["status"] != "waiting_for_user":
            reason = manual_event["status"]
        if recording:
            recording.finish(reason)
        active["controller"] = False
        active.update(control_stop=None, control_done=None)
        done.set()
        try:
            await ws.close()
        except (RuntimeError,WebSocketDisconnect):
            pass
