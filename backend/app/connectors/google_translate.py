"""Минимальный коннектор Google Translate для внутреннего перевода RFQ."""

from __future__ import annotations

import httpx


class GoogleTranslateError(RuntimeError):
    """Google Translate недоступен или вернул неожиданный ответ."""


class GoogleTranslateConnector:
    endpoint = "https://translate.googleapis.com/translate_a/single"

    def __init__(
        self,
        *,
        timeout_s: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.transport = transport

    def translate(
        self,
        text: str,
        *,
        source_language: str = "en",
        target_language: str = "ru",
    ) -> str:
        source = text.strip()
        if not source:
            raise GoogleTranslateError("Текст для перевода пуст")
        try:
            with httpx.Client(
                timeout=self.timeout_s,
                transport=self.transport,
            ) as client:
                response = client.post(
                    self.endpoint,
                    params={
                        "client": "gtx",
                        "sl": source_language,
                        "tl": target_language,
                        "dt": "t",
                    },
                    data={"q": source},
                )
                response.raise_for_status()
                payload = response.json()
            segments = payload[0]
            translated = "".join(
                segment[0]
                for segment in segments
                if isinstance(segment, list) and segment and isinstance(segment[0], str)
            ).strip()
        except (httpx.HTTPError, ValueError, TypeError, IndexError) as exc:
            raise GoogleTranslateError("Некорректный ответ Google Translate") from exc
        if not translated:
            raise GoogleTranslateError("Google Translate вернул пустой перевод")
        return translated
