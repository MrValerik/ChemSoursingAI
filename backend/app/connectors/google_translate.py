"""Google Translate для внутреннего перевода с повторами временных сбоев."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep

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
        max_attempts: int = 3,
        retry_delay_s: float = 0.25,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.timeout_s = timeout_s
        self.transport = transport
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_s = max(0.0, retry_delay_s)
        self.sleeper = sleeper

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
        last_error: Exception | None = None
        with httpx.Client(
            timeout=self.timeout_s,
            transport=self.transport,
        ) as client:
            for attempt in range(self.max_attempts):
                try:
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
                        if isinstance(segment, list)
                        and segment
                        and isinstance(segment[0], str)
                    ).strip()
                    if not translated:
                        raise GoogleTranslateError(
                            "Google Translate вернул пустой перевод"
                        )
                    return translated
                except (
                    httpx.HTTPError,
                    GoogleTranslateError,
                    ValueError,
                    TypeError,
                    IndexError,
                    KeyError,
                ) as exc:
                    last_error = exc
                    if attempt + 1 < self.max_attempts:
                        self.sleeper(self.retry_delay_s * (2**attempt))

        raise GoogleTranslateError(
            "Google Translate временно недоступен или вернул некорректный ответ"
        ) from last_error
