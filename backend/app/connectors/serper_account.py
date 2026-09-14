"""Read Serper account balance without consuming search credits."""

from datetime import datetime, timezone
from threading import Lock
from time import monotonic

import httpx

from app.core.config import get_settings
from app.schemas.serper import SerperBalanceRead

_lock = Lock()
_cached: SerperBalanceRead | None = None
_cache_identity: tuple[str, str] | None = None
_expires_at = 0.0


def read_balance() -> SerperBalanceRead:
    global _cached, _cache_identity, _expires_at
    settings = get_settings()
    if not settings.serper_api_key:
        return SerperBalanceRead(status="not_configured")
    identity = (settings.serper_base_url, settings.serper_api_key)
    with _lock:
        if _cached is not None and identity == _cache_identity and monotonic() < _expires_at:
            return _cached
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.get(
                    settings.serper_base_url.rstrip("/") + "/account",
                    headers={"X-API-KEY": settings.serper_api_key},
                )
                response.raise_for_status()
                payload = response.json()
            balance = payload.get("balance") if isinstance(payload, dict) else None
            if type(balance) is not int or balance < 0:
                raise ValueError("Invalid balance")
            result = SerperBalanceRead(
                status="ok", remaining_credits=balance,
                checked_at=datetime.now(timezone.utc),
            )
        except (httpx.HTTPError, ValueError):
            # Never expose provider bodies, headers, or credentials.
            result = SerperBalanceRead(status="unavailable")
        _cached, _cache_identity = result, identity
        _expires_at = monotonic() + settings.serper_balance_cache_seconds
        return result
