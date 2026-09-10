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
from verification import attempt_slider
from job_lifecycle import run_connected
from manual import router as manual_router, active, wait_for_human

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


class RestartSearch(Exception):
    """Retry a rejected verification from the home page in a new Chrome process."""


async def ready(page, mouse, events, can_restart=False):
    if not await needs_verification(page):
        return True
    if os.getenv("ECHEMI_AUTO_VERIFY", "true").lower() == "true":
        if await attempt_slider(page, mouse, events):
            return True
        if not await needs_verification(page):
            return True
        if can_restart:
            raise RestartSearch()
    return await wait_for_human(page, events)

async def collect(query, output):
    attempts = min(3, max(1, int(os.getenv("ECHEMI_AUTO_ATTEMPTS", "3"))))
    for attempt in range(1, attempts + 1):
        event_start = len(output["diagnostics"]["captcha"])
        output["diagnostics"]["browser_attempt"] = attempt
        output["diagnostics"]["max_browser_attempts"] = attempts
        try:
            await collect_once(query, output, can_restart=attempt < attempts)
            return
        except RestartSearch:
            output["diagnostics"].setdefault("restarts", []).append(
                {"after_attempt": attempt, "reason": "verification_rejected"})
            for row in output["results"]:
                if row["detail_status"] == "reading":
                    row["detail_status"] = "pending"
            output["message"] = f"Проверка не пройдена. Заново открываем Echemi: попытка {attempt+1} из {attempts}."
            await asyncio.sleep(3)
        finally:
            for event in output["diagnostics"]["captcha"][event_start:]:
                event["browser_attempt"] = attempt


async def collect_once(query, output, can_restart=False):
    async with async_playwright() as p, open_chrome(p, PROFILE) as context:
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.bring_to_front()
            await page.set_viewport_size({"width": 1280, "height": 900})
            page.set_default_timeout(25000)
            output["diagnostics"]["browser_launch"] = "chrome_cdp"
            output['diagnostics']['browser_version'] = await page.evaluate('navigator.userAgent')
            output['diagnostics'].setdefault('verification_responses', [])
            async def observe(response):
                host = urlsplit(response.url).hostname or ''
                if host.endswith('.aliyuncs.com') and 'captcha' in host:
                    try:
                        safe = verification_result(await response.json())
                        if safe and len(output['diagnostics']['verification_responses']) < 30:
                            output['diagnostics']['verification_responses'].append(safe)
                    except Exception:
                        pass
            page.on('response', observe)
            mouse = Mouse(page)
            await page.goto("https://www.echemi.com/",wait_until="domcontentloaded",timeout=60000)
            await asyncio.sleep(8)
            await mouse.go(230,270,2)
            await mouse.go(580,400,2.4)
            await mouse.go(790,290,1.8)
            if not await ready(page,mouse,output["diagnostics"]["captcha"],can_restart):
                output.update(status="partial" if output["results"] else "blocked",message="Echemi не пропустил проверку на главной странице.")
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
            if not await ready(page,mouse,output["diagnostics"]["captcha"],can_restart):
                output.update(status="partial" if output["results"] else "blocked",message="Echemi не пропустил проверку в поисковой выдаче.")
                return
            blocks = await page.evaluate(_BLOCKS)
            output["diagnostics"].update(listing_count=len(blocks),limit=LIMIT,scope="first_page")
            seen = {r["product_url"] for r in output["results"]}
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
                if row["detail_status"] == "read":
                    continue
                url = row["product_url"]
                output["message"] = f"Найдено товаров: {len(output['results'])}. Читаем карточку {index} из {len(output['results'])}."
                row["detail_status"] = "reading"
                try:
                    await asyncio.sleep(random.uniform(PAUSE_MIN,PAUSE_MAX))
                    await page.goto(url,wait_until="domcontentloaded",timeout=60000)
                    await asyncio.sleep(5)
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],can_restart):
                        row["detail_status"] = "blocked"
                        continue
                    for _ in range(3):
                        await mouse.go(random.uniform(700,950),random.uniform(300,550))
                        await page.mouse.wheel(0,random.randint(350,600))
                        await asyncio.sleep(random.uniform(1,2))
                    if not await ready(page,mouse,output["diagnostics"]["captcha"],can_restart):
                        row["detail_status"] = "blocked"
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
                except RestartSearch:
                    raise
                except Exception as exc:
                    row.update(detail_status="failed",error_type=type(exc).__name__)
            incomplete = any(r["detail_status"] != "read" for r in output["results"])
            output.update(status="partial" if incomplete else "completed",
                          message="Часть карточек недоступна. Сохранены данные выдачи." if incomplete else
                          ("Сбор первой страницы завершён." if output["results"] else "Товары в выдаче не найдены."))
        finally:
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
            await run_connected(connection, collect(request.query.strip(),output), timeout=900)
        except Exception as exc:
            output.update(status="partial" if output["results"] else "failed",
                          message="Сбор прерван по времени или из-за ошибки браузера.")
            output["diagnostics"]["error_type"] = type(exc).__name__
        finally:
            active.update(id=None, waiting=False, page=None, output=None)
    return output
