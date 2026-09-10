import httpx
from app.core.config import get_settings


class EchemiBrowserBusy(Exception):
    """The browser declined the job before starting it; retry is safe."""


def search_echemi(query: str, search_id: int) -> dict:
    settings = get_settings()
    with httpx.Client(timeout=930, trust_env=False) as client:
        response = client.post(settings.echemi_browser_url.rstrip("/") + "/search",
                               json={"query": query, "search_id": search_id})
        if response.status_code == 409:
            raise EchemiBrowserBusy()
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") not in {
        "completed", "partial", "blocked", "failed"
    } or not isinstance(payload.get("results"), list):
        raise ValueError("Invalid Echemi response")
    return payload


async def get_manual_status(search_id: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(get_settings().echemi_browser_url.rstrip("/") + f"/manual/{search_id}")
            response.raise_for_status()
            data = response.json()
            return {"waiting": data.get("waiting") is True,
                    "remaining_seconds": max(0, min(600, int(data.get("remaining_seconds", 0))))}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Manual browser unavailable") from exc


def get_search_progress(search_id: int) -> dict | None:
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(get_settings().echemi_browser_url.rstrip("/") + f"/search/{search_id}/progress")
        if response.status_code == 404:
            return None  # Job has not started, or the final response is already on its way.
        response.raise_for_status()
        payload = response.json()
    if (not isinstance(payload, dict) or payload.get("search_id") != search_id
            or not isinstance(payload.get("results"), list)
            or not isinstance(payload.get("diagnostics"), dict)):
        raise ValueError("Invalid Echemi progress")
    return payload


def manual_connection(search_id: int):
    from urllib.parse import urlsplit, urlunsplit
    from websockets.legacy.client import connect
    url = urlsplit(get_settings().echemi_browser_url)
    target = urlunsplit(("wss" if url.scheme == "https" else "ws", url.netloc,
                        url.path.rstrip("/") + f"/manual/{search_id}", "", ""))
    return connect(target, open_timeout=5, max_size=2**22)
