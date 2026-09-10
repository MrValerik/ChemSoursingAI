import httpx
from app.core.config import get_settings


def search_echemi(query: str) -> dict:
    settings = get_settings()
    with httpx.Client(timeout=930, trust_env=False) as client:
        response = client.post(settings.echemi_browser_url.rstrip("/") + "/search",
                               json={"query": query})
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") not in {
        "completed", "partial", "blocked", "failed"
    } or not isinstance(payload.get("results"), list):
        raise ValueError("Invalid Echemi response")
    return payload
