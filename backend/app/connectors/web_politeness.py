"""Правила вежливого обхода: robots.txt и пауза между запросами к домену.

Соблюдение robots.txt — не только этика: сайт, который мы уважаем, реже
блокирует нас в будущем. Недоступный robots.txt не считается запретом, иначе
защита от ботов на самом robots.txt закрывала бы весь домен.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

_ROBOTS_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0)
_ROBOTS_CACHE_TTL_S = 3600.0
_DEFAULT_DELAY_S = 1.0

_lock = threading.Lock()
_robots_cache: dict[str, tuple[float, RobotFileParser | None, float | None]] = {}
_last_request_at: dict[str, float] = {}


def _origin(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}", host


def _load_robots(origin: str, user_agent: str) -> tuple[RobotFileParser | None, float | None]:
    """Читает robots.txt. None означает «правил нет», а не «всё запрещено»."""
    try:
        response = httpx.get(
            f"{origin}/robots.txt",
            timeout=_ROBOTS_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
    except httpx.HTTPError:
        return None, None
    if response.status_code >= 400 or not response.text:
        return None, None

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    delay = None
    try:
        raw_delay = parser.crawl_delay("*")
        delay = float(raw_delay) if raw_delay is not None else None
    except Exception:  # pragma: no cover - формат robots бывает нестандартным
        delay = None
    return parser, delay


def robots_verdict(url: str, user_agent: str) -> tuple[bool, float | None]:
    """Возвращает (разрешено ли, требуемая пауза) по robots.txt домена."""
    origin, host = _origin(url)
    if not host:
        return True, None
    now = time.monotonic()
    with _lock:
        cached = _robots_cache.get(origin)
        if cached is not None and now - cached[0] < _ROBOTS_CACHE_TTL_S:
            parser, delay = cached[1], cached[2]
        else:
            parser, delay = None, None
            cached = None
    if cached is None:
        parser, delay = _load_robots(origin, user_agent)
        with _lock:
            _robots_cache[origin] = (now, parser, delay)
    if parser is None:
        return True, None
    return parser.can_fetch(user_agent, url), delay


def wait_for_domain_slot(url: str, delay_s: float | None = None) -> None:
    """Выдерживает паузу между запросами к одному домену."""
    _, host = _origin(url)
    if not host:
        return
    wait_for = delay_s if delay_s is not None else _DEFAULT_DELAY_S
    # Слишком большой crawl-delay заблокировал бы этап целиком.
    wait_for = max(0.0, min(wait_for, 10.0))
    while True:
        with _lock:
            last = _last_request_at.get(host)
            now = time.monotonic()
            if last is None or now - last >= wait_for:
                _last_request_at[host] = now
                return
            remaining = wait_for - (now - last)
        time.sleep(remaining)


def reset_politeness_state() -> None:
    """Сбрасывает кэш robots.txt и историю пауз (используется в тестах)."""
    with _lock:
        _robots_cache.clear()
        _last_request_at.clear()
