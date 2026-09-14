"""Low-latency manual control on the service's dedicated X display."""
import asyncio
import base64
import json
import time

from fastapi import WebSocket, WebSocketDisconnect

import manual
from manual_recording import client_time
from page_state import needs_verification


async def control(ws: WebSocket, search_id: int):
    await ws.accept()
    active = manual.active
    if active['id'] != search_id or not active['waiting'] or active['controller']:
        await ws.close(code=4409)
        return
    active['controller'] = True
    stop, done = asyncio.Event(), asyncio.Event()
    active.update(control_stop=stop, control_done=done)
    page = active['page']
    actual = getattr(page, '_page', page)
    event = active['manual_event']
    recording = active['recording_budget'].start(event)
    pressed, reason, session = False, 'disconnected', None
    latest, changed, background = None, asyncio.Event(), set()

    async def frame(data):
        nonlocal latest, reason
        try:
            await session.send('Page.screencastFrameAck', {'sessionId': data['sessionId']})
            metadata = data.get('metadata', {})
            if (metadata.get('deviceWidth'), metadata.get('deviceHeight')) != (1280, 900):
                reason = 'frame_geometry_mismatch'
                stop.set()
                return
            latest = base64.b64decode(data['data'], validate=True)
            changed.set()
        except Exception:
            reason = 'connection_error'
            stop.set()

    def got_frame(data):
        nonlocal reason
        if stop.is_set():
            return
        if len(background) >= 3:
            reason = 'frame_transport_overloaded'
            stop.set()
            return
        task = asyncio.create_task(frame(data))
        background.add(task)
        task.add_done_callback(background.discard)

    async def frames():
        await ws.send_bytes(await actual.screenshot(type='jpeg', quality=60))
        while not stop.is_set():
            await changed.wait()
            changed.clear()
            await ws.send_bytes(latest)

    async def watch():
        nonlocal reason
        while not stop.is_set():
            if active['id'] != search_id or not active['waiting']:
                stop.set()
                return
            if not await needs_verification(actual):
                reason = 'challenge_disappeared'
                stop.set()
                return
            await asyncio.sleep(.1)

    async def receive():
        nonlocal pressed, reason
        while active['waiting'] and not stop.is_set():
            raw = await ws.receive_text()
            received = time.monotonic()
            if len(raw) > 300:
                raise ValueError('Event too large')
            message = json.loads(raw)
            x, y = manual.coordinates(message)
            timestamp = client_time(message)
            if stop.is_set() or not active['waiting'] or active['id'] != search_id:
                return
            # Check the live page before each press; moves must not queue behind DOM calls.
            if message['type'] == 'down' and not await needs_verification(actual):
                reason = 'challenge_disappeared'
                stop.set()
                return
            point = recording.append(message['type'], x, y, timestamp, received) if recording else None
            await page.mouse.move(x, y)
            if message['type'] == 'down' and not pressed:
                await page.mouse.down()
                pressed = True
            elif message['type'] == 'up':
                await page.mouse.up()
                pressed = False
            if recording:
                recording.delivered(point)
            await ws.send_json({'type': 'ack', 'client_t_ms': timestamp, 'x': x, 'y': y})
            if recording and (message['type'] != 'move' or len(recording.data['events']) % 10 == 0):
                await ws.send_json(recording.state())

    tasks = []
    try:
        session = await actual.context.new_cdp_session(actual)
        session.on('Page.screencastFrame', got_frame)
        await session.send('Page.startScreencast', {'format': 'jpeg', 'quality': 60,
            'maxWidth': 1280, 'maxHeight': 900, 'everyNthFrame': 1})
        await ws.send_json(recording.state() if recording else {
            'type': 'recording', 'limited': True, 'event_count': 0})
        tasks = [asyncio.create_task(fn()) for fn in (frames, watch, receive, stop.wait)]
        completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in completed:
            task.result()
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        reason = 'interrupted'
        raise
    except Exception:
        reason = 'connection_error'
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if pressed:
            try:
                await page.mouse.up()
            except Exception:
                reason = 'connection_error'
        if session:
            try:
                await session.send('Page.stopScreencast')
            except Exception:
                pass
            finally:
                try:
                    await session.detach()
                except Exception:
                    pass
        for task in background:
            task.cancel()
        await asyncio.gather(*list(background), return_exceptions=True)
        if event['status'] != 'waiting_for_user':
            reason = event['status']
        if recording:
            recording.finish(reason)
        active['controller'] = False
        active.update(control_stop=None, control_done=None)
        done.set()
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
