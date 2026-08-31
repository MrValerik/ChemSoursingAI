"""Выбор модели только для общения и документов из переписки."""

from app.core.config import get_settings
from app.extraction.llm_client import LLMClient


def communication_llm_client() -> LLMClient:
    """Меняет модель, но сохраняет провайдера, авторизацию и лимиты LLM_*."""
    settings = get_settings()
    model = settings.communication_llm_model.strip()
    if not model:
        return LLMClient()
    return LLMClient(
        model=model,
        thinking_control=settings.communication_llm_thinking_control,
    )
