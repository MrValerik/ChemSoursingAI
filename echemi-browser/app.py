"""Internal Echemi-only browser. No database or mail credentials in this service."""
import asyncio
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
from diagnostics import public_url, verification_result
from parsing import parse_detail, _BLOCKS, parse_offer, product_url, is_verification, is_valid_cas

from chrome_runtime import open_chrome
from page_state import needs_verification
from job_lifecycle import run_connected
from manual import router as manual_router, active, wait_for_human
from captcha_context import CaptchaContext
from captcha_probe import CaptchaProbe

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


class Mouse:
    def __init__(self, page):
        self.page, self.x, self.y = page, 0., 0.

    async def go(self, x, y, seconds=1.6):
        ax, ay = self.x, self.y
        began = time.monotonic()
        count = int(seconds * 50)
        for i in range(1, count + 1):
            u = i / count
            f = u*u*(3-2*u)
            await asyncio.sleep(max(0, seconds*u - (time.monotonic()-began)))
            await self.page.mouse.move(ax+(x-ax)*f, ay+(y-ay)*f+8*math.sin(math.pi*u))
        self.x, self.y = x, y


async def ready(page, mouse, events, context=None, probe=None):
    if not await needs_verification(page):
        return True
    if probe is not None:
        return await probe.run(page, mouse, events)
    if context is not None:
        try:
            await context.capture()
            events.append({"mode": "context_observation", "context": context.summary()})
        except Exception:
            events.append({"mode": "context_observation", "status": "unavailable"})
    return await wait_for_human(page, events)

async def collect(query, output, captcha_probe_attempts=0):
    async with async_playwright() as p, open_chrome(p, PROFILE) as context:
        challenge = None
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.set_viewport_size({"width": 1280, "height": 900})
            page.set_default_timeout(25000)
            output["diagnostics"]["browser_launch"] = "chrome_cdp"
            output['diagnostics']['browser_version'] = await page.evaluate('navigator.userAgent')
            challenge = CaptchaContext(page, output['diagnostics'])
            probe = CaptchaProbe(challenge, captcha_probe_attempts) if captcha_probe_attempts else None
            output['diagnostics']['captcha_probe_attempts'] = captcha_probe_attempts
            mouse = Mouse(page)
            await page.goto("https://www.echemi.com/",wait_until="domcontentloaded",timeout=60000)
            await asyncio.sleep(8)
            await mouse.go(230,270,2)
            await mouse.go(580,400,2.4)
            await mouse.go(790,290,1.8)
            if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe):
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
            if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe):
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
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe):
                        row["detail_status"] = "blocked"
                        if probe is not None:
                            break
                        continue
                    for _ in range(3):
                        await mouse.go(random.uniform(700,950),random.uniform(300,550))
                        await page.mouse.wheel(0,random.randint(350,600))
                        await asyncio.sleep(random.uniform(1,2))
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],challenge,probe):
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
            if challenge is not None:
                await challenge.close()
            active.update(waiting=False, page=None)


@app.get("/health")
async def health():
    return {"status":"ok"}


@app.get("/search/{search_id}/progress")
async def progress(search_id: int):
    output = active.get("output")
    if active.get("id") != search_id or output is None:
        raise HTTPException(404, "No active search")
    snapshot = deepcopy(output)
    if active.get("waiting"):
        snapshot["message"] = "Нужна ручная проверка Echemi. Найденные товары уже сохранены."
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
        try:
            await run_connected(connection, collect(request.query.strip(),output,request.captcha_probe_attempts), timeout=900)
        except Exception as exc:
            output.update(status="partial" if output["results"] else "failed",
                          message="Сбор прерван по времени или из-за ошибки браузера.")
            output["diagnostics"]["error_type"] = type(exc).__name__
        finally:
            active.update(id=None, waiting=False, page=None, output=None)
    return output
