"""One bounded slider attempt; provider acceptance is recorded separately from page access."""
import asyncio
import json
import math
import random
import time
from pathlib import Path
from urllib.parse import urlsplit

from diagnostics import public_url, verification_result
from page_state import needs_verification


def sampled_trajectory(points):
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("Missing trajectory")
    for point in points:
        if not isinstance(point, list) or len(point) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in point
        ):
            raise ValueError("Invalid trajectory point")
    if points[0] != [0, 0, 0] or not 0 < points[-1][0] <= 10 or points[-1][1] <= 0:
        raise ValueError("Invalid trajectory endpoints")
    if any(b[0] < a[0] for a, b in zip(points, points[1:])):
        raise ValueError("Nonmonotonic trajectory")
    sampled = [points[0]]
    for point in points[1:-1]:
        if point[0] - sampled[-1][0] >= 1 / 30:
            sampled.append(point)
    return sampled + [points[-1]]


def scaled_trajectory(points, handle, track, viewport):
    if not handle or not track or track["width"] <= handle["width"]:
        raise ValueError("Invalid slider geometry")
    sx = handle["x"] + handle["width"] * .25
    sy = handle["y"] + handle["height"] * .525
    scale = (track["width"] - handle["width"]) / points[-1][1]
    result = [(dt, sx + x * scale, sy + y * scale) for dt, x, y in points]
    if any(not (0 <= x < viewport["w"] and 0 <= y < viewport["h"]) for _, x, y in result):
        raise ValueError("Trajectory outside viewport")
    return result


async def attempt_slider(page, mouse, events, timeout=65):
    event = {"mode": "automatic", "url": public_url(page.url), "status": "attempting",
             "slider_attempted": False, "verification_responses": []}
    events.append(event)
    down = False

    async def observe(response):
        host = urlsplit(response.url).hostname or ""
        if event["slider_attempted"] and host.endswith(".aliyuncs.com") and "captcha" in host:
            try:
                safe = verification_result(await response.json())
                if safe and len(event["verification_responses"]) < 10:
                    event["verification_responses"].append(safe)
            except Exception:
                pass

    async def perform():
        nonlocal down
        handle = page.locator("#aliyunCaptcha-sliding-slider")
        track = page.locator("#aliyunCaptcha-sliding-body")
        await handle.wait_for(state="visible", timeout=25000)
        await handle.scroll_into_view_if_needed()
        await asyncio.sleep(2)
        h, t = await handle.bounding_box(), await track.bounding_box()
        await asyncio.sleep(1)
        if not h or h != await handle.bounding_box():
            raise ValueError("Unstable slider")
        vp = await page.evaluate("({w:innerWidth,h:innerHeight})")
        raw = json.loads(Path(__file__).with_name("trajectory.json").read_text())
        points = scaled_trajectory(sampled_trajectory(raw), h, t, vp)
        event.update(original_points=len(raw), sent_points=len(points), target_seconds=points[-1][0])
        for _ in range(2):
            await mouse.go(min(vp["w"]-20, max(20, h["x"]+random.uniform(-100,100))),
                           min(vp["h"]-30, max(20, h["y"]-random.uniform(35,100))))
        await mouse.go(points[0][1], points[0][2])
        await asyncio.sleep(.7)
        event["slider_attempted"] = True
        down = True
        await page.mouse.down()
        began = time.monotonic()
        for dt, x, y in points[1:]:
            await asyncio.sleep(max(0, dt-(time.monotonic()-began)))
            await page.mouse.move(x, y)
            mouse.x, mouse.y = x, y
        await page.mouse.up()
        down = False
        event["drag_seconds"] = round(time.monotonic()-began, 3)
        await asyncio.sleep(12)
        accepted = any(r.get("verify_code") == "T001" and r.get("verify_result") is True
                       for r in event["verification_responses"])
        passed = accepted and not await needs_verification(page)
        event["status"] = "passed" if passed else "not_passed"
        return passed

    page.on("response", observe)
    try:
        return await asyncio.wait_for(perform(), timeout)
    except asyncio.CancelledError:
        event["status"] = "cancelled"
        raise
    except Exception as exc:
        event.update(status="not_passed", error_type=type(exc).__name__)
        return False
    finally:
        page.remove_listener("response", observe)
        if down and not page.is_closed():
            try:
                await asyncio.wait_for(page.mouse.up(), 3)
            except Exception:
                pass
