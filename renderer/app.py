"""Отрисовка страницы браузером — запасной путь загрузки первичных страниц.

Зачем отдельная служба. Часть сайтов присылает не страницу, а программу:
замер 3 сентября 2026 по пяти адресам, на которых загрузчик сообщал «на
странице не найден текст», дал ответы 200 длиной 151–985 байт, состоящие
из упакованного скрипта, без единой картинки и без единого символа текста.
Обычный HTTP-клиент скачивает байты и разбирает HTML; браузер вдобавок
выполняет скрипт, и только после этого в документе появляется содержимое.

Почему службой, а не библиотекой в бэкенде. Здесь исполняется чужой
JavaScript с сайтов поставщиков, и держать его в одном контейнере с базой
и почтой незачем. Заодно четыре поисковых воркера делят один браузер, а не
поднимают по своему.

Отдаётся текст, а не картинка: система доказательств сличает цитату с
сохранённым текстом дословно, и подменять его снимком экрана нельзя.
Проверку robots.txt делает вызывающая сторона до обращения сюда — эта
служба сама решений о допустимости не принимает.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright

MAX_TEXT_CHARS = 200_000
DEFAULT_TIMEOUT_MS = int(os.getenv("RENDER_TIMEOUT_MS", "20000"))
# Ресурсы, которые ничего не добавляют к тексту, но тратят время и трафик.
BLOCKED_RESOURCES = {"image", "media", "font"}

app = FastAPI(title="ChemSource page renderer")
_playwright = None
_browser = None
_lock = asyncio.Lock()


class RenderRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, ge=1000, le=60000)


class RenderResponse(BaseModel):
    final_url: str
    title: str | None
    text: str
    http_status: int | None


async def _get_browser():
    global _playwright, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            if _playwright is None:
                _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        return _browser


@app.get("/health")
async def health() -> dict:
    browser = await _get_browser()
    return {"status": "ok", "browser": browser.is_connected()}


@app.post("/render", response_model=RenderResponse)
async def render(request: RenderRequest) -> RenderResponse:
    browser = await _get_browser()
    context = await browser.new_context(
        locale="en-US",
        viewport={"width": 1280, "height": 2000},
    )
    try:
        page = await context.new_page()

        async def skip(route):
            if route.request.resource_type in BLOCKED_RESOURCES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", skip)
        response = await page.goto(
            request.url,
            wait_until="domcontentloaded",
            timeout=request.timeout_ms,
        )
        # Часть сайтов дописывает содержимое сразу после разбора документа.
        # Ждём затишья в сети, но не считаем его обязательным: страница с
        # вечным опросом сервера иначе не отдаст ничего.
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=min(5000, request.timeout_ms)
            )
        except Exception:
            pass
        title = await page.title()
        text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )
        return RenderResponse(
            final_url=page.url,
            title=title or None,
            text=(text or "")[:MAX_TEXT_CHARS],
            http_status=response.status if response is not None else None,
        )
    finally:
        await context.close()
