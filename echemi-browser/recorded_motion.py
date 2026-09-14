"""Replay a private, validated single gesture against the current slider geometry."""
import asyncio
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from page_state import needs_verification


def number(value, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError('Invalid recording number')
    return value


def load_recordings(path):
    if not path:
        raise ValueError('ECHEMI_CAPTCHA_RECORDINGS_FILE is required for recorded_v4')
    with Path(path).open('rb') as stream:
        raw = stream.read(512001)
    if len(raw) > 512000:
        raise ValueError('Recording library too large')
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data['version'] != 1):
        raise ValueError('Invalid recording library version')
    traces = data.get('traces')
    if not isinstance(traces, list) or not 1 <= len(traces) <= 10:
        raise ValueError('Invalid recording count')
    identifiers = set()
    for trace in traces:
        if not isinstance(trace, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', trace.get('id', '')):
            raise ValueError('Invalid recording identifier')
        if trace['id'] in identifiers:
            raise ValueError('Duplicate recording identifier')
        identifiers.add(trace['id'])
        handle, track = trace['handle'], trace['track']
        for box in (handle, track):
            number(box['x'], 0, 1279)
            number(box['y'], 0, 899)
            number(box['width'], 1, 1280 - box['x'])
            number(box['height'], 1, 900 - box['y'])
        events = trace['events']
        if not isinstance(events, list) or not 3 <= len(events) <= 2000:
            raise ValueError('Invalid gesture length')
        pressed, released, previous = False, False, -1
        for event in events:
            timestamp = number(event['t'], 0, 30)
            if timestamp < previous or released:
                raise ValueError('Invalid gesture sequence')
            previous = timestamp
            x, y = number(event['x'], 0, 1279), number(event['y'], 0, 899)
            kind = event['type']
            if kind == 'down':
                if pressed or not (handle['x'] <= x < handle['x'] + handle['width']
                        and handle['y'] <= y < handle['y'] + handle['height']):
                    raise ValueError('Press must be on the original handle')
                pressed = True
            elif kind == 'up':
                if not pressed:
                    raise ValueError('Release without press')
                released = True
            elif kind != 'move':
                raise ValueError('Invalid gesture event')
        if not released:
            raise ValueError('Incomplete gesture')
    return traces, hashlib.sha256(raw).hexdigest()[:16]


def mapped_events(trace, handle, track, viewport):
    # Translate only. Stretching a recorded drag silently changes its velocity.
    for actual, original in ((handle, trace['handle']), (track, trace['track'])):
        if any(abs(actual[k] - original[k]) > 3 for k in ('width', 'height')):
            raise ValueError('Recording does not match slider dimensions')
    if any(abs((handle[k] - track[k]) - (trace['handle'][k] - trace['track'][k])) > 3
           for k in ('x', 'y')):
        raise ValueError('Recording does not match slider alignment')
    dx, dy = handle['x'] - trace['handle']['x'], handle['y'] - trace['handle']['y']
    mapped = [dict(event, x=event['x'] + dx, y=event['y'] + dy) for event in trace['events']]
    if any(not (0 <= event['x'] < viewport['w'] and 0 <= event['y'] < viewport['h']) for event in mapped):
        raise ValueError('Recorded gesture outside viewport')
    return mapped


async def replay(page, mouse, context, epoch, handle, track, viewport, metrics):
    traces, digest = load_recordings(os.getenv('ECHEMI_CAPTCHA_RECORDINGS_FILE', ''))
    trace = traces[(metrics.get('attempt', 1) - 1) % len(traces)]
    events = mapped_events(trace, handle, track, viewport)
    metrics.update(profile='recorded_v4', recording_id=trace['id'], library_id=digest,
                   handle=handle, track=track, source_events=len(events), events_sent=0,
                   planned_seconds=events[-1]['t'], max_lateness_ms=0)
    began = time.monotonic()
    pressed = False
    try:
        for event in events:
            await asyncio.sleep(max(0, began + event['t'] - time.monotonic()))
            if context.epoch != epoch:
                raise ValueError('Challenge changed during recorded gesture')
            if event['type'] == 'down':
                if not await needs_verification(page):
                    raise ValueError('Challenge disappeared before press')
                current = await page.locator('#aliyunCaptcha-sliding-slider').bounding_box()
                if current != handle or not await context.capture() or context.epoch != epoch:
                    raise ValueError('Challenge moved before press')
            await page.mouse.move(event['x'], event['y'])
            mouse.x, mouse.y = event['x'], event['y']
            if event['type'] == 'down':
                await page.mouse.down()
                pressed = True
                metrics['pressed'] = True
            elif event['type'] == 'up':
                await page.mouse.up()
                pressed = False
            metrics['events_sent'] += 1
            metrics['max_lateness_ms'] = round(max(metrics['max_lateness_ms'],
                1000 * (time.monotonic() - began - event['t'])), 1)
    finally:
        if pressed:
            await page.mouse.up()
        metrics['actual_seconds'] = round(time.monotonic() - began, 3)
    return metrics['actual_seconds']
