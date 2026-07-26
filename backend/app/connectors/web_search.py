"""Независимый от ключей web-поиск для MVP через HTML-выдачу DuckDuckGo."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

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
        if direct in seen:
            continue
        seen.add(direct)
        results.append(
            {"title": _clean(title), "url": direct, "snippet": _clean(snippet)}
        )
        if len(results) >= limit:
            break
    return results


def search_web(query: str, limit: int = 8) -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChemSourceAI/1.0)"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get("https://html.duckduckgo.com/html/", params={"q": query})
        response.raise_for_status()
    return parse_search_results(response.text, limit)
