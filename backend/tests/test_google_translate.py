import json

import httpx
import pytest

from app.connectors.google_translate import (
    GoogleTranslateConnector,
    GoogleTranslateError,
)


def test_google_translate_sends_english_rfq_and_returns_russian_text():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                [
                    ["Тема: Запрос цены\n\n", "Subject: Request for quotation\n\n"],
                    ["Пожалуйста, предложите 50 кг аммиака.", "Please quote 50 kg of ammonia."],
                ],
                None,
                "en",
            ],
        )

    connector = GoogleTranslateConnector(transport=httpx.MockTransport(handler))
    translated = connector.translate(
        "Subject: Request for quotation\n\nPlease quote 50 kg of ammonia."
    )

    assert translated == "Тема: Запрос цены\n\nПожалуйста, предложите 50 кг аммиака."
    assert requests[0].url.params["sl"] == "en"
    assert requests[0].url.params["tl"] == "ru"
    assert requests[0].url.params["dt"] == "t"
    assert b"50+kg" in requests[0].content


def test_google_translate_rejects_empty_or_invalid_response():
    connector = GoogleTranslateConnector(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=json.dumps([[], None, "en"]).encode(),
            )
        )
    )

    with pytest.raises(GoogleTranslateError):
        connector.translate("Hello")
    with pytest.raises(GoogleTranslateError):
        connector.translate("   ")
