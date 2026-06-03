"""Health/readiness эндпоинты."""

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    """Liveness-проба: приложение запущено."""
    return {"status": "ok", "version": __version__}


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
