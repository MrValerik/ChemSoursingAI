import json

import httpx
import pytest

from app.connectors.whatsapp import (
    WhatsAppConfigurationError,
    WhatsAppConnector,
)
from app.core.config import get_settings


def _settings():
    return get_settings().model_copy(
        update={
            "whatsapp_token": "secret-token",
            "whatsapp_phone_id": "123456789",
            "whatsapp_api_base_url": "https://graph.facebook.com",
            "whatsapp_api_version": "v23.0",
        }
    )


def test_check_health_and_send_text():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "display_phone_number": "+1 555 0100",
                    "verified_name": "ChemSource Test",
                },
            )
        return httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test-1"}]},
        )

    connector = WhatsAppConnector(
        _settings(), transport=httpx.MockTransport(handler)
    )
    health = connector.check_health()
    assert health["verified_name"] == "ChemSource Test"
    assert connector.send_text(to_number="+7 (900) 000-00-00", body="Hello") == (
        "wamid.test-1"
    )
    assert [request.method for request in requests] == ["GET", "POST"]
    assert json.loads(requests[1].content)["messaging_product"] == "whatsapp"


def test_send_rejects_invalid_recipient_before_network():
    connector = WhatsAppConnector(_settings())
    with pytest.raises(WhatsAppConfigurationError):
        connector.send_text(to_number="123", body="Hello")
