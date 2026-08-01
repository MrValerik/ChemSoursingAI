"""Независимый от ключей web-поиск для MVP через HTML-выдачу DuckDuckGo."""

from __future__ import annotations

import html
import re
from time import sleep
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.services.domain_rate_limit import (
    defer_domain,
    reserve_slot,
    retry_after_seconds,
)

_THROTTLED_STATUSES = {403, 429, 503}
# Пауза, когда сервис ограничил нас, но Retry-After не прислал.
_THROTTLED_BACKOFF_S = 30.0

_LINK_RE = re.compile(
    r'<a\b(?=[^>]*class="[^"]*\bresult__a\b)[^>]*'
    r'href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'<(?:a|div)\b(?=[^>]*class="[^"]*\bresult__snippet\b)[^>]*>'
    r'(.*?)</(?:a|div)>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(value: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split())


def _direct_url(value: str) -> str:
    decoded = html.unescape(value)
    parsed = urlparse(decoded)
    target = parse_qs(parsed.query).get("uddg")
    return unquote(target[0]) if target else decoded


def parse_search_results(page: str, limit: int = 8) -> list[dict]:
    """Разбирает только ссылки и текст; HTML никогда не исполняется."""
    results: list[dict] = []
    seen: set[str] = set()
    links = list(_LINK_RE.finditer(page))
    for index, match in enumerate(links):
        url, title = match.groups()
        region_end = links[index + 1].start() if index + 1 < len(links) else len(page)
        snippet_match = _SNIPPET_RE.search(page, match.end(), region_end)
        snippet = snippet_match.group(1) if snippet_match else ""
        direct = _direct_url(url)
        if urlparse(direct).scheme.lower() not in {"http", "https"}:
            continue
        if direct in seen:
            continue
        seen.add(direct)
        results.append(
            {"title": _clean(title), "url": direct, "snippet": _clean(snippet)}
        )
        if len(results) >= limit:
            break
    return results


_SEARCH_URL = "https://html.duckduckgo.com/html/"


def search_web(query: str, limit: int = 8) -> list[dict]:
    """Запрашивает выдачу, соблюдая общую для всех процессов паузу.

    Это единственный внешний адрес, к которому обращается каждый поиск и
    каждый запрос его плана — до двенадцати обращений за один запуск. Лимит
    поисковика действует на исходящий IP, поэтому пауза здесь обязана быть
    общей для всех worker-процессов, а не отдельной у каждого.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChemSourceAI/1.0)"}
    wait = reserve_slot(_SEARCH_URL)
    if wait > 0:
        sleep(wait)
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get(_SEARCH_URL, params={"q": query})
        if response.status_code in _THROTTLED_STATUSES:
            # Сервис сам сказал, когда к нему возвращаться. Отметка видна
            # всем процессам, иначе соседний worker продолжит стучаться.
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            defer_domain(_SEARCH_URL, delay or _THROTTLED_BACKOFF_S)
        response.raise_for_status()
    return parse_search_results(response.text, limit)
