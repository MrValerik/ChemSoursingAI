"""Health/readiness эндпоинты."""

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import get_settings
from app.extraction.llm_client import LLMClient

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    """Liveness-проба: приложение запущено."""
    return {"status": "ok", "version": __version__}


@router.get("/health/llm")
def llm_health(response: Response) -> dict:
    """Readiness-проба Qwen; проверяет API модели, но не запускает генерацию."""
    settings = get_settings()
    available, _ = LLMClient().check_health()
    if not available:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    # В публичный health-check не выводим внутренний URL и текст сетевой ошибки.
    return {
        "status": "ok" if available else "unavailable",
        "model": settings.llm_model,
    }


@router.get("/info")
def info() -> dict:
    """Базовая информация о среде (без секретов)."""
    s = get_settings()
    return {
        "version": __version__,
        "env": s.app_env,
        "llm_model": s.llm_model,
        "pubchem_base_url": s.pubchem_base_url,
    }
