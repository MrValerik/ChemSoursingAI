"""LLM-клиент: вызов локальной модели через OpenAI-совместимый эндпоинт.

Модель (llama-server / vLLM) отдаёт /v1/chat/completions. Клиент не знает о
конкретной модели — только адрес из конфига. Просим structured output по
QUOTE_JSON_SCHEMA; при недоступности модели бросаем LLMUnavailableError,
чтобы оркестратор переключился на fallback.
"""

from __future__ import annotations

import json

import httpx

from app.core.config import get_settings
from app.extraction.schema import QUOTE_JSON_SCHEMA

_SYSTEM_PROMPT = (
    "You extract a structured price quotation from a chemical supplier's reply. "
    "Return ONLY the fields defined by the schema. Use null when a value is not "
    "present. Currency must be an ISO code (USD, EUR, CNY). Do not invent values."
)


class LLMUnavailableError(RuntimeError):
    """Модель недоступна (нет соединения/таймаут/ошибка сервера)."""


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.model = model or s.llm_model
        self.api_key = api_key or s.llm_api_key
        self.timeout_s = timeout_s if timeout_s is not None else s.llm_timeout_s

    def extract_quote(self, email_text: str) -> dict:
        """Запрашивает у модели структурированную котировку. Возвращает dict
        по QUOTE_JSON_SCHEMA. Бросает LLMUnavailableError при проблемах связи."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": email_text},
            ],
            "temperature": 0,
            # Structured output: формат строго по JSON-схеме котировки.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "quotation",
                    "schema": QUOTE_JSON_SCHEMA,
                    "strict": True,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            raise LLMUnavailableError(str(exc)) from exc

        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LLMUnavailableError(f"bad LLM response: {exc}") from exc
