"""Независимый от ключей web-поиск для MVP через HTML-выдачу DuckDuckGo."""

from __future__ import annotations

import html
import re
from time import sleep
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import get_settings
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

# Разметка, которой выдача сообщает о честно пустом результате. Её наличие
# отличает «ничего не найдено» от страницы, подсунутой антибот-защитой.
_EMPTY_RESULT_MARKERS = ("no-results", "результатов не найдено", "no results found")
# Признаки того, что вместо выдачи пришла страница проверки.
_BLOCK_MARKERS = ("anomaly", "captcha", "unusual traffic", "verify you are human")


class SearchSourceBlocked(RuntimeError):
    """Источник ответил, но выдачи не отдал.

    Поисковик отвечает 200 и антибот-страницей вместо результатов, поэтому
    «ничего не найдено» и «нас не пускают» выглядят одинаково. Разделять их
    обязательно: пустой список по реальному веществу закупщик прочитает как
    факт рынка, а не как отказ источника.
    """


def looks_blocked(page: str, parsed_count: int) -> bool:
    """Похож ли ответ на отказ доступа, а не на пустую выдачу."""
    if parsed_count:
        return False
    lowered = page.lower()
    if any(marker in lowered for marker in _BLOCK_MARKERS):
        return True
    # Честно пустая выдача содержит собственное сообщение об этом. Ответ без
    # результатов и без такого сообщения — это не выдача.
    return not any(marker in lowered for marker in _EMPTY_RESULT_MARKERS)


class UnknownSearchProvider(RuntimeError):
    """В настройках указан источник выдачи, которого нет в коде."""


class DuckDuckGoHtmlProvider:
    """HTML-выдача DuckDuckGo: работает без ключа, но не имеет квоты и SLA.

    Замер на стенде показал, почему это не промышленный источник: под
    нагрузкой двух worker-процессов 37 запросов из 48 вернулись пустыми, а
    те же запросы позже отдавали по восемь результатов. Провайдер оставлен
    как значение по умолчанию для разработки и как запасной вариант.
    """

    name = "duckduckgo_html"

    def search(self, query: str, limit: int = 8) -> list[dict]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ChemSourceAI/1.0)"}
        # Пауза общая для всех процессов: лимит поисковика действует на
        # исходящий IP, а не на отдельный worker.
        wait = reserve_slot(_SEARCH_URL)
        if wait > 0:
            sleep(wait)
        with httpx.Client(
            timeout=20, follow_redirects=True, headers=headers
        ) as client:
            response = client.get(_SEARCH_URL, params={"q": query})
            if response.status_code in _THROTTLED_STATUSES:
                # Сервис сам сказал, когда к нему возвращаться. Отметка видна
                # всем процессам, иначе соседний worker продолжит стучаться.
                delay = retry_after_seconds(response.headers.get("Retry-After"))
                defer_domain(_SEARCH_URL, delay or _THROTTLED_BACKOFF_S)
            response.raise_for_status()
        results = parse_search_results(response.text, limit)
        if looks_blocked(response.text, len(results)):
            # Продолжать долбить заблокировавший нас источник бессмысленно, а
            # соседние процессы должны узнать об этом тоже.
            defer_domain(_SEARCH_URL, _THROTTLED_BACKOFF_S)
            raise SearchSourceBlocked(
                "Поисковая выдача вернула страницу без результатов и без "
                "сообщения о пустом ответе — вероятно, доступ ограничен"
            )
        return results


class SearchProviderNotConfigured(RuntimeError):
    """Провайдер выбран, но не хватает обязательной настройки."""


class SerperProvider:
    """Выдача Google через API Serper: есть квота, ключ и предсказуемый ответ.

    В отличие от скрейпинга, отказ здесь выражен кодом ответа, а не
    подсунутой страницей, поэтому «нас не пускают» и «ничего не найдено»
    различаются на уровне протокола.
    """

    name = "serper"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.serper_api_key
        self.base_url = settings.serper_base_url.rstrip("/")
        self.region = settings.serper_region
        self.language = settings.serper_language
        if not self.api_key:
            raise SearchProviderNotConfigured(
                "Для источника serper не задан SERPER_API_KEY"
            )

    def search(self, query: str, limit: int = 8) -> list[dict]:
        url = f"{self.base_url}/search"
        # Квота считается на ключ, но вежливая пауза к домену остаётся общей:
        # несколько worker-процессов не должны выбирать её очередями.
        wait = reserve_slot(url, _SERPER_INTERVAL_S)
        if wait > 0:
            sleep(wait)
        payload = {
            "q": query,
            "gl": self.region,
            "hl": self.language,
            "num": max(10, limit),
        }
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=25) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code in _THROTTLED_STATUSES:
                delay = retry_after_seconds(response.headers.get("Retry-After"))
                defer_domain(url, delay or _THROTTLED_BACKOFF_S)
                raise SearchSourceBlocked(
                    "Serper ограничил доступ или исчерпана квота "
                    f"(HTTP {response.status_code})"
                )
            response.raise_for_status()
            body = response.json()
        results: list[dict] = []
        seen: set[str] = set()
        for item in body.get("organic") or []:
            link = str(item.get("link") or "")
            if urlparse(link).scheme.lower() not in {"http", "https"}:
                continue
            if link in seen:
                continue
            seen.add(link)
            results.append(
                {
                    "title": _clean(str(item.get("title") or "")),
                    "url": link,
                    "snippet": _clean(str(item.get("snippet") or "")),
                }
            )
            if len(results) >= limit:
                break
        return results


# Пауза к API мягче, чем к скрейпингу: квота считается по ключу, а не по IP.
_SERPER_INTERVAL_S = 0.2

_PROVIDERS: dict[str, type] = {
    DuckDuckGoHtmlProvider.name: DuckDuckGoHtmlProvider,
    SerperProvider.name: SerperProvider,
}


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))


def get_search_provider(name: str | None = None):
    """Возвращает источник выдачи, выбранный конфигурацией.

    Источник — это настройка, а не код: смена поисковика не должна затрагивать
    конвейер. Имя провайдера попадает в трассировку запуска, поэтому по
    сохранённому поиску видно, чем именно он выполнялся.
    """
    key = (name or get_settings().search_provider).strip()
    factory = _PROVIDERS.get(key)
    if factory is None:
        raise UnknownSearchProvider(
            f"Неизвестный источник выдачи {key!r}; доступны: "
            + ", ".join(available_providers())
        )
    return factory()


def search_web(query: str, limit: int = 8) -> list[dict]:
    """Ищет через источник, заданный настройками."""
    return get_search_provider().search(query, limit)
