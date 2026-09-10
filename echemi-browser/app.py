"""Internal Echemi-only browser. No database or mail credentials in this service."""
import asyncio
import json
import math
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
from diagnostics import public_url, verification_result
from parsing import parse_detail, _BLOCKS, parse_offer, product_url, is_verification, is_valid_cas

app = FastAPI()
busy = asyncio.Lock()
LIMIT = min(20, max(1, int(os.getenv("ECHEMI_MAX_RESULTS", "10"))))
PROFILE = os.getenv("ECHEMI_PROFILE_DIR", "/data/profile")
PAUSE_MIN = max(3, float(os.getenv("ECHEMI_PAUSE_MIN", "8")))
PAUSE_MAX = max(PAUSE_MIN, float(os.getenv("ECHEMI_PAUSE_MAX", "14")))


class Search(BaseModel):
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


async def ready(page, mouse, events):
    if not is_verification(await page.locator("body").inner_text(), await page.title()):
        return True
    event = {"url": public_url(page.url), "status": "waiting", "slider_attempted": False}
    events.append(event)
    try:
        handle = page.locator("#aliyunCaptcha-sliding-slider")
        track = page.locator("#aliyunCaptcha-sliding-body")
        await handle.wait_for(state="visible", timeout=25000)
        await handle.scroll_into_view_if_needed()
        await asyncio.sleep(2)
        h, t = await handle.bounding_box(), await track.bounding_box()
        await asyncio.sleep(1)
        if not h or not t or h != await handle.bounding_box():
            raise ValueError("Unstable slider")
        vp = await page.evaluate("({w:innerWidth,h:innerHeight})")
        for _ in range(2):
            await mouse.go(min(vp["w"]-20,max(20,h["x"]+random.uniform(-100,100))),
                           min(vp["h"]-30,max(20,h["y"]-random.uniform(35,100))))
        sx, sy = h["x"]+h["width"]*.25, h["y"]+h["height"]*.525
        scale = (t["width"]-h["width"])/280
        points = json.loads(Path(__file__).with_name("trajectory.json").read_text())
        if any(not (0 <= sx+x*scale < vp["w"] and 0 <= sy+y*scale < vp["h"]) for _,x,y in points):
            raise ValueError("Trajectory outside viewport")
        await mouse.go(sx,sy)
        await asyncio.sleep(.7)
        event["slider_attempted"] = True
        await page.mouse.down()
        began = time.monotonic()
        try:
            for dt,x,y in points[1:]:
                await asyncio.sleep(max(0,dt-(time.monotonic()-began)))
                mouse.x, mouse.y = sx+x*scale,sy+y*scale
                await page.mouse.move(mouse.x,mouse.y)
        finally:
            await page.mouse.up()
        event["drag_seconds"] = round(time.monotonic()-began, 3)
        await asyncio.sleep(12)
        passed = not is_verification(await page.locator("body").inner_text(),await page.title())
        event["status"] = "passed" if passed else "not_passed"
        return passed
    except Exception as exc:
        event.update(status="not_passed", error_type=type(exc).__name__)
        return False





async def collect(query, output):
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PROFILE, headless=os.getenv("ECHEMI_HEADLESS","false")=="true",
            locale="ru", viewport={"width":1280,"height":900},
            args=["--disable-dev-shm-usage"])
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(25000)
            output['diagnostics']['browser_version'] = await page.evaluate('navigator.userAgent')
            output['diagnostics']['verification_responses'] = []
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
            if not await ready(page,mouse,output["diagnostics"]["captcha"]):
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
            if not await ready(page,mouse,output["diagnostics"]["captcha"]):
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
                try:
                    await asyncio.sleep(random.uniform(PAUSE_MIN,PAUSE_MAX))
                    await page.goto(url,wait_until="domcontentloaded",timeout=60000)
                    await asyncio.sleep(5)
                    if not await ready(page,mouse,output["diagnostics"]["captcha"]):
                        row["detail_status"] = "blocked"
                        continue
                    for _ in range(3):
                        await mouse.go(random.uniform(700,950),random.uniform(300,550))
                        await page.mouse.wheel(0,random.randint(350,600))
                        await asyncio.sleep(random.uniform(1,2))
                    if not await ready(page,mouse,output["diagnostics"]["captcha"]):
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
                except Exception as exc:
                    row.update(detail_status="failed",error_type=type(exc).__name__)
            incomplete = any(r["detail_status"] != "read" for r in output["results"])
            output.update(status="partial" if incomplete else "completed",
                          message="Часть карточек недоступна. Сохранены данные выдачи." if incomplete else
                          ("Сбор первой страницы завершён." if output["results"] else "Товары в выдаче не найдены."))
        finally:
            await context.close()


@app.get("/health")
async def health():
    return {"status":"ok"}


@app.post("/search")
async def search(request: Search):
    if not request.query.strip():
        raise HTTPException(422,"Empty query")
    if busy.locked():
        raise HTTPException(409,"Browser busy")
    output = {"status":"running","results":[],"diagnostics":{"captcha":[]}}
    async with busy:
        try:
            await asyncio.wait_for(collect(request.query.strip(),output),timeout=900)
        except Exception as exc:
            output.update(status="partial" if output["results"] else "failed",
                          message="Сбор прерван по времени или из-за ошибки браузера.")
            output["diagnostics"]["error_type"] = type(exc).__name__
    return output
