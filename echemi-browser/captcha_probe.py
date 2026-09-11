"""Bounded server experiment using the page's own Alibaba SDK and fresh context."""
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from page_state import needs_verification


async def protected_content(page):
    url = urlsplit(page.url)
    if url.scheme != "https" or url.hostname not in {"www.echemi.com", "echemi.com"}:
        return False
    return (not await needs_verification(page)
            and await page.locator("#topSearchKeywords").is_visible())


def accepted(outcome, content_available):
    return bool(content_available and outcome and outcome.get("verify_result") is True
                and outcome.get("verify_code") == "T001")


async def drag(page, mouse, context, epoch):
    handle = page.locator("#aliyunCaptcha-sliding-slider")
    track = page.locator("#aliyunCaptcha-sliding-body")
    await handle.wait_for(state="visible", timeout=15000)
    await handle.scroll_into_view_if_needed()
    h, t = await handle.bounding_box(), await track.bounding_box()
    await asyncio.sleep(.4)
    if not h or not t or h != await handle.bounding_box():
        raise ValueError("Unstable slider")
    distance = t["width"] - h["width"]
    if distance <= 0:
        raise ValueError("Invalid slider geometry")
    sx, sy = h["x"] + h["width"] / 2, h["y"] + h["height"] / 2
    vp = await page.evaluate("({w:innerWidth,h:innerHeight})")
    points = json.loads(Path(__file__).with_name("trajectory.json").read_text())
    # The historical recording ends beyond the track; clamp to the measured endpoint.
    path = [(dt, sx + min(max(x / 280, 0), 1) * distance, sy + y)
            for dt, x, y in points]
    if any(not (0 <= x < vp["w"] and 0 <= y < vp["h"]) for _, x, y in path):
        raise ValueError("Trajectory outside viewport")
    await mouse.go(sx, sy)
    if context.epoch != epoch or not await context.capture():
        raise ValueError("Challenge changed before drag")
    await page.mouse.down()
    began = time.monotonic()
    try:
        for dt, x, y in path[1:]:
            await asyncio.sleep(max(0, dt - (time.monotonic() - began)))
            if context.epoch != epoch:
                raise ValueError("Challenge changed during drag")
            await page.mouse.move(x, y)
            mouse.x, mouse.y = x, y
    finally:
        await page.mouse.up()
    return round(time.monotonic() - began, 3)


class CaptchaProbe:
    def __init__(self, context, attempts):
        self.context = context
        self.remaining = min(3, max(0, attempts))

    async def run(self, page, mouse, events):
        while self.remaining:
            self.remaining -= 1
            event = {"mode": "automatic_probe", "status": "preparing", "slider_attempted": False}
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
                    return False
                after = len(self.context.responses)
                event["status"] = "dragging"
                event["slider_attempted"] = True
                event["drag_seconds"] = await drag(page, mouse, self.context, epoch)
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
                return False
            finally:
                event["elapsed_seconds"] = round(time.monotonic() - began, 3)
            if self.remaining:
                # Reload through the browser. The page SDK creates/signs a new challenge.
                # No saved token or recorded HTTP verification request is replayed.
                await asyncio.sleep(3)
                await page.reload(wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(3)
                if await protected_content(page):
                    events.append({"mode": "automatic_probe", "status": "access_after_reload",
                                   "slider_attempted": False})
                    return True
        return False
