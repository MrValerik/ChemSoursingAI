"""Internal Echemi-only browser. No database or mail credentials in this service."""
import asyncio
import hashlib
from copy import deepcopy
import math
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
from diagnostics import public_url, verification_result, rejection_message
from pointer_motion import move_continuous, move_timed
from parsing import parse_detail, _BLOCKS, parse_offer, product_url, is_verification, is_valid_cas

from chrome_runtime import open_chrome, new_job_page
from page_state import needs_verification
from job_lifecycle import run_connected
from manual import router as manual_router, active, wait_for_human
from manual_recording import RecordingBudget
from captcha_context import CaptchaContext
from captcha_probe import CaptchaProbe, MOTION_PROFILE

app = FastAPI()
app.include_router(manual_router)
busy = asyncio.Lock()
LIMIT = min(20, max(1, int(os.getenv("ECHEMI_MAX_RESULTS", "10"))))
PROFILE = os.getenv("ECHEMI_PROFILE_DIR", "/data/chrome-cdp-profile")
PAUSE_MIN = max(3, float(os.getenv("ECHEMI_PAUSE_MIN", "8")))
PAUSE_MAX = max(PAUSE_MIN, float(os.getenv("ECHEMI_PAUSE_MAX", "14")))


class Search(BaseModel):
    search_id: int = Field(gt=0)
    query: str = Field(min_length=1, max_length=200)
    captcha_probe_attempts: int = Field(default=0, ge=0, le=3, strict=True)
    captcha_manual_fallback: bool = Field(default=False, strict=True)


class Inquiry(BaseModel):
    job_id: int = Field(gt=0)
    product_url: str = Field(max_length=1000)
    sender: dict[str, str | bool | None]  # Backend sends the validated snapshot.
    message: str = Field(min_length=20, max_length=5000)
    seller_name: str = Field(min_length=1, max_length=500)
    captcha_probe_attempts: int = Field(default=0, ge=0, le=3, strict=True)


@app.post("/inquiry")
async def inquiry(payload: Inquiry):
    if product_url(payload.product_url) != payload.product_url:
        raise HTTPException(422, "Invalid product URL")
    if busy.locked():
        raise HTTPException(409, "Browser busy")
    async with busy:
        from inquiry import submit
        async with async_playwright() as p, open_chrome(p, PROFILE) as context:
            page = await new_job_page(context)
            page.set_default_timeout(10000)
            from inquiry_verification import InquiryVerification, allowed_request
            verification = InquiryVerification(page, Mouse(page), payload.captcha_probe_attempts)
            async def restrict_submission(route):
                if allowed_request(route.request):
                    await route.continue_()
                else:
                    verification.blocked(route.request)
                    await route.abort()
            await page.route("**/*", restrict_submission)
            try:
                return await asyncio.wait_for(submit(page, payload.product_url, payload.sender, payload.message,
                                                    seller_name=payload.seller_name, verify=verification.check), 300)
            except asyncio.TimeoutError:
                return {"status": "unknown"}
            finally:
                await verification.close()
                await page.close()


class Mouse:
    def __init__(self, page):
        self.page = page
        self.x, self.y = getattr(page.mouse, 'x', 0.), getattr(page.mouse, 'y', 0.)

    async def go(self, x, y, seconds=1.6):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Invalid pointer duration")
        ax, ay = self.x, self.y
        count = max(1, int(seconds * 50))
        points = []
        for i in range(count + 1):
            u = i / count
            f = u*u*(3-2*u)
            points.append((seconds*u, ax+(x-ax)*f, ay+(y-ay)*f+8*math.sin(math.pi*u)))
        def update(px, py):
            self.x, self.y = px, py
        if MOTION_PROFILE == 'smooth_v3':
            await move_continuous(self.page.mouse, points, max_step=6, update=update)
        else:
            await move_timed(self.page.mouse, points, update=update)


async def ready(page, mouse, events, context=None, probe=None, manual_fallback=False, stage="unknown"):
    if not await needs_verification(page):
        return True
    if probe is not None:
        try:
            if await probe.run(page, mouse, events, stage=stage):
                return True
        except Exception as exc:
            events.append({"mode": "automatic_probe", "status": "failed",
                           "error_type": type(exc).__name__})
        if not manual_fallback:
            return False
        events.append({"mode": "automatic_fallback", "status": "manual_required",
                       "reason": "attempts_exhausted" if not probe.remaining else "not_confirmed"})
    if context is not None:
        try:
            await context.capture()
            events.append({"mode": "context_observation", "context": context.summary()})
        except Exception:
            events.append({"mode": "context_observation", "status": "unavailable"})
    return await wait_for_human(page, events, stage=stage)

async def collect(query, output, captcha_probe_attempts=0, captcha_manual_fallback=False):
    async with async_playwright() as p, open_chrome(p, PROFILE) as context:
        challenge = None
        try:
            page = await new_job_page(context)
            page.set_default_timeout(25000)
            output["diagnostics"]["browser_launch"] = "chrome_cdp"
            output['diagnostics']['browser_version'] = await page.evaluate('navigator.userAgent')
            output['diagnostics']['browser_environment'] = await page.evaluate(
                '({webdriver:navigator.webdriver,language:navigator.language,platform:navigator.platform,'
                'viewport:[innerWidth,innerHeight]})')
            output['diagnostics']['profile_id'] = hashlib.sha256(PROFILE.encode()).hexdigest()[:16]
            output['diagnostics']['pointer_backend'] = os.getenv('ECHEMI_POINTER_BACKEND', 'cdp')
            output['diagnostics']['motion_profile'] = MOTION_PROFILE
            output['diagnostics']['pointer_playback'] = ('bounded_steps_v1' if MOTION_PROFILE == 'smooth_v3'
                                                         else 'recorded_events_v1' if MOTION_PROFILE == 'recorded_v4'
                                                         else 'elapsed_time_v1')
            challenge = CaptchaContext(page, output['diagnostics'])
            probe = CaptchaProbe(challenge, captcha_probe_attempts) if captcha_probe_attempts else None
            output['diagnostics']['captcha_probe_attempts'] = captcha_probe_attempts
            output['diagnostics']['captcha_manual_fallback'] = captcha_manual_fallback
            mouse = Mouse(page)
            await page.goto("https://www.echemi.com/",wait_until="domcontentloaded",timeout=60000)
            await asyncio.sleep(2 if MOTION_PROFILE == 'recorded_v4' else 8)
            if MOTION_PROFILE != 'recorded_v4':
                await mouse.go(230,270,2)
                await mouse.go(580,400,2.4)
                await mouse.go(790,290,1.8)
            if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe,captcha_manual_fallback,stage="home"):
                output.update(status="blocked",message="Echemi не пропустил проверку на главной странице.")
                return
            field = page.locator("#topSearchKeywords")
            box = await field.bounding_box()
            if box:
                await mouse.go(box["x"]+box["width"]*.45,box["y"]+box["height"]/2)
            await field.fill("")
            await field.press_sequentially(query,delay=180)
            await asyncio.sleep(2)
            await field.press("Enter")
            await asyncio.sleep(10)
            if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe,captcha_manual_fallback,stage="listing"):
                output.update(status="blocked",message="Echemi не пропустил проверку в поисковой выдаче.")
                return
            blocks = await page.evaluate(_BLOCKS)
            output["diagnostics"].update(listing_count=len(blocks),limit=LIMIT,scope="first_page")
            seen = set()
            for block in blocks:
                url = product_url(block["url"])
                if not url or url in seen:
                    continue
                seen.add(url)
                if len(output["results"]) >= LIMIT:
                    break
                row = parse_offer(block,query=query,observed_at=datetime.now(timezone.utc).isoformat())
                row["detail_status"] = "pending"
                output["results"].append(row)
            for index, row in enumerate(output["results"], start=1):
                url = row["product_url"]
                output["message"] = f"Найдено товаров: {len(output['results'])}. Читаем карточку {index} из {len(output['results'])}."
                row["detail_status"] = "reading"
                try:
                    await asyncio.sleep(random.uniform(PAUSE_MIN,PAUSE_MAX))
                    await page.goto(url,wait_until="domcontentloaded",timeout=60000)
                    await asyncio.sleep(5)
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe,captcha_manual_fallback,stage="detail"):
                        row["detail_status"] = "blocked"
                        if probe is not None:
                            break
                        continue
                    for _ in range(3):
                        await mouse.go(random.uniform(700,950),random.uniform(300,550))
                        await page.mouse.wheel(0,random.randint(350,600))
                        await asyncio.sleep(random.uniform(1,2))
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe,captcha_manual_fallback,stage="detail_after_scroll"):
                        row["detail_status"] = "blocked"
                        if probe is not None:
                            break
                        continue
                    if product_url(page.url) != url:
                        row["detail_status"] = "redirected"
                        continue
                    raw = await page.evaluate("""() => ({
                        title:document.querySelector('h1')?.innerText || document.title,
                        language:document.documentElement.lang || null, text:document.body.innerText,
                        links:[...document.querySelectorAll('a[href]')].filter(a=>a.getClientRects().length)
                          .map(a=>({text:a.innerText.trim(),href:a.href}))})""")
                    row["detail"] = parse_detail(raw,page.url)
                    row["title"] = row["detail"]["title"]
                    row["detail_status"] = "read"
                except Exception as exc:
                    row.update(detail_status="failed",error_type=type(exc).__name__)
            incomplete = any(r["detail_status"] != "read" for r in output["results"])
            output.update(status="partial" if incomplete else "completed",
                          message="Часть карточек недоступна. Сохранены данные выдачи." if incomplete else
                          ("Сбор первой страницы завершён." if output["results"] else "Товары в выдаче не найдены."))
        finally:
            if output.get('status') in {'blocked', 'partial'}:
                reason = rejection_message(output['diagnostics']['captcha'])
                if reason:
                    output['message'] = (output.get('message', '') + ' ' + reason).strip()
            if challenge is not None:
                await challenge.close()
            active.update(waiting=False, page=None)


@app.get("/health")
async def health():
    if MOTION_PROFILE == 'recorded_v4':
        from recorded_motion import load_recordings
        load_recordings(os.getenv('ECHEMI_CAPTCHA_RECORDINGS_FILE', ''))
    return {"status":"ok"}


@app.get("/search/{search_id}/progress")
async def progress(search_id: int):
    output = active.get("output")
    if active.get("id") != search_id or output is None:
        raise HTTPException(404, "No active search")
    snapshot = deepcopy(output)
    events = snapshot["diagnostics"].get("captcha", [])
    snapshot.setdefault("message", "Открываем Echemi и читаем карточки. Это может занять несколько минут.")
    if active.get("waiting"):
        fallback = any(e.get("mode") == "automatic_fallback" for e in events)
        snapshot["message"] = ("Автоматическая проверка Echemi не завершилась. Доступна ручная проверка."
                               if fallback else "Нужна ручная проверка Echemi.")
        reason = rejection_message(events)
        if reason:
            snapshot['message'] = reason + ' Доступна ручная проверка.'
        if snapshot["results"]:
            snapshot["message"] += " Найденные товары уже сохранены."
    elif events and events[-1].get("mode") == "automatic_probe" and events[-1].get("status") in {"preparing", "dragging"}:
        event = events[-1]
        snapshot["message"] = (f"Автоматически проходим проверку Echemi: попытка {event['attempt']} "
                               f"из {event['attempt_limit']}. Поиск продолжится автоматически.")
    return {"search_id": search_id, **snapshot}


@app.post("/search")
async def search(request: Search, connection: Request):
    if not request.query.strip():
        raise HTTPException(422,"Empty query")
    if busy.locked():
        raise HTTPException(409,"Browser busy")
    output = {"status":"running","results":[],"diagnostics":{"captcha":[]}}
    async with busy:
        active['id'] = request.search_id
        active['output'] = output
        active['recording_budget'] = RecordingBudget()
        try:
            await run_connected(connection, collect(request.query.strip(),output,request.captcha_probe_attempts,
                                                     request.captcha_manual_fallback), timeout=900)
        except Exception as exc:
            output.update(status="partial" if output["results"] else "failed",
                          message="Сбор прерван по времени или из-за ошибки браузера.")
            output["diagnostics"]["error_type"] = type(exc).__name__
        finally:
            active.update(id=None, waiting=False, page=None, output=None, recording_budget=None, manual_event=None)
    return output
