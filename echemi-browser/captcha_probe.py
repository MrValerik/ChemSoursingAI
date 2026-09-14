"""Bounded CAPTCHA attempts using the page's own Alibaba SDK and fresh context."""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from page_state import needs_verification
from diagnostics import public_url
from pointer_motion import move_timed, slider_path
from calibrated_motion import calibrated_segments, regrip_segment

MOTION_PROFILE = os.getenv('ECHEMI_CAPTCHA_MOTION_PROFILE', 'calibrated_v2')
if MOTION_PROFILE not in {'legacy_v1', 'calibrated_v2'}:
    raise ValueError('Invalid ECHEMI_CAPTCHA_MOTION_PROFILE')


async def protected_content(page):
    url = urlsplit(page.url)
    if url.scheme != "https" or url.hostname not in {"www.echemi.com", "echemi.com"}:
        return False
    return (not await needs_verification(page)
            and await page.locator("#topSearchKeywords").is_visible())


def accepted(outcome, content_available):
    return bool(content_available and outcome and outcome.get("verify_result") is True
                and outcome.get("verify_code") == "T001")


async def drag(page, mouse, context, epoch, metrics=None):
    metrics = metrics if metrics is not None else {}
    handle = page.locator("#aliyunCaptcha-sliding-slider")
    track = page.locator("#aliyunCaptcha-sliding-body")
    await handle.wait_for(state="visible", timeout=15000)
    await handle.scroll_into_view_if_needed()
    h, t = await handle.bounding_box(), await track.bounding_box()
    await asyncio.sleep(.4)
    if not h or not t or h != await handle.bounding_box() or t != await track.bounding_box():
        raise ValueError("Unstable slider")
    vp = await page.evaluate("({w:innerWidth,h:innerHeight})")
    if MOTION_PROFILE == 'calibrated_v2':
        segments = calibrated_segments(h, t)
    else:
        points = json.loads(Path(__file__).with_name("trajectory.json").read_text(encoding='utf-8'))
        segments = [slider_path(points, h, t)]
    if any(not (0 <= x < vp["w"] and 0 <= y < vp["h"]) for path in segments for _, x, y in path):
        raise ValueError("Trajectory outside viewport")
    await mouse.go(*segments[0][0][1:])
    if context.epoch != epoch or not await context.capture():
        raise ValueError("Challenge changed before drag")
    metrics.update(profile=MOTION_PROFILE, handle=h, track=t, target=list(segments[-1][-1][1:]),
                   planned_seconds=round(sum(part[-1][0] for part in segments) + .015 * (len(segments)-1), 3),
                   moves_sent=0, max_move_seconds=0, segments=[], regrips=0)
    def check():
        if context.epoch != epoch:
            raise ValueError("Challenge changed during drag")
    def update(x, y):
        mouse.x, mouse.y = x, y
    began = time.monotonic()
    try:
        for index, path in enumerate(segments):
            if index:
                await page.mouse.up()
                await asyncio.sleep(.015)
                # Read the actual handle position after release/animation. Never
                # click its old position or reuse a newly initialized challenge.
                check()
                box = await handle.bounding_box()
                metrics['regrip_handle'] = box
                path = regrip_segment(path, box, h, t)
                x, y = path[0][1:]
                metrics['regrip_start'] = [x, y]
                await page.mouse.move(x, y)
                update(x, y)
                check()
            await page.mouse.down()
            metrics['pressed'] = True
            metrics['regrips'] = index
            part = {}
            metrics['segments'].append(part)
            try:
                await move_timed(page.mouse, path, check=check, update=update, metrics=part)
            finally:
                metrics['moves_sent'] += part.get('moves_sent', 0)
                metrics['max_move_seconds'] = max(metrics['max_move_seconds'], part.get('max_move_seconds', 0))
    finally:
        await page.mouse.up()
        metrics['actual_seconds'] = round(time.monotonic() - began, 3)
    return metrics['actual_seconds']


async def refresh_challenge(page, mouse, context):
    """Try the same widget refresh used by the person; verify a fresh init."""
    before = context.epoch
    refresh = page.locator('#aliyunCaptcha-sliding-refresh')
    if await refresh.is_visible():
        box = await refresh.bounding_box()
        if box:
            await mouse.go(box['x'] + box['width']/2, box['y'] + box['height']/2, .7)
            try:
                await page.mouse.down()
                await asyncio.sleep(.09)
            finally:
                await page.mouse.up()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if context.epoch != before and await context.capture():
                    return {'method': 'widget', 'fresh_context': True}
                await asyncio.sleep(.1)
    await page.reload(wait_until='domcontentloaded', timeout=60000)
    await asyncio.sleep(3)
    return {'method': 'page_reload', 'fresh_context': context.epoch != before}


class CaptchaProbe:
    def __init__(self, context, attempts):
        self.context = context
        self.limit = min(3, max(0, attempts))
        self.remaining = self.limit

    async def run(self, page, mouse, events, stage="unknown"):
        while self.remaining:
            self.remaining -= 1
            event = {"mode": "automatic_probe", "status": "preparing", "slider_attempted": False,
                     "attempt": self.limit - self.remaining, "attempt_limit": self.limit,
                     "stage": stage, "page_url": public_url(page.url), "motion": {}}
            events.append(event)
            began = time.monotonic()
            try:
                deadline = began + 15
                fresh = await self.context.capture()
                while not fresh and time.monotonic() < deadline:
                    await asyncio.sleep(.25)
                    fresh = await self.context.capture()
                event["context"] = self.context.summary()
                epoch = self.context.epoch
                if not fresh:
                    event["status"] = "context_incomplete"
                else:
                    after = len(self.context.responses)
                    event["status"] = "dragging"
                    event["drag_seconds"] = await drag(page, mouse, self.context, epoch, metrics=event['motion'])
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        outcome = self.context.outcome(epoch, after)
                        if accepted(outcome, await protected_content(page)):
                            event.update(status="passed", verification=outcome)
                            return True
                        if outcome and outcome.get("verify_result") is False:
                            event.update(status="rejected", verification=outcome)
                            break
                        await asyncio.sleep(.25)
                    else:
                        event["status"] = "not_confirmed"
            except Exception as exc:
                event.update(status="failed", error_type=type(exc).__name__)
            finally:
                event['slider_attempted'] = event['motion'].get('pressed', False)
                event["elapsed_seconds"] = round(time.monotonic() - began, 3)
            if self.remaining:
                await asyncio.sleep(1 if MOTION_PROFILE == 'calibrated_v2' else 3)
                if MOTION_PROFILE == 'calibrated_v2':
                    event['retry'] = await refresh_challenge(page, mouse, self.context)
                else:
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(3)
                if await protected_content(page):
                    events.append({"mode": "automatic_probe", "status": "access_after_reload",
                                   "slider_attempted": False})
                    return True
        return False
