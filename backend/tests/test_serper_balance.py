from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.settings import router
from app.connectors import serper_account as account
from app.models.enums import UserRole


@pytest.fixture
def provider(monkeypatch):
    settings = SimpleNamespace(serper_api_key="synthetic-key",
        serper_base_url="https://google.serper.dev", serper_balance_cache_seconds=60)
    monkeypatch.setattr(account, "get_settings", lambda: settings)
    monkeypatch.setattr(account, "_cached", None)
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(account.httpx, "Client", lambda **kwargs: client)
    def reply(payload, status=200):
        client.get.return_value = httpx.Response(status, json=payload,
            request=httpx.Request("GET", "https://google.serper.dev/account"))
    reply({"balance": 2500})
    return settings, client, reply


@pytest.mark.parametrize("value", [2500, 0])
def test_balance_and_cache(provider, monkeypatch, value):
    settings, client, reply = provider
    reply({"balance": value, "rateLimit": 5})
    monkeypatch.setattr(account, "monotonic", lambda: 100)
    result = account.read_balance()
    assert result.remaining_credits == value and result.status == "ok"
    assert result.checked_at is not None
    assert account.read_balance() == result
    assert client.get.call_count == 1
    monkeypatch.setattr(account, "monotonic", lambda: 161)
    account.read_balance()
    assert client.get.call_count == 2
    settings.serper_api_key = "another-synthetic-key"
    account.read_balance()
    assert client.get.call_count == 3


@pytest.mark.parametrize("payload", [{}, {"balance": -1}, {"balance": True},
    {"balance": "2500"}, {"balance": 1.5}, [], None])
def test_malformed_balance_is_not_zero(provider, payload):
    _, _, reply = provider
    reply(payload)
    result = account.read_balance()
    assert result.status == "unavailable" and result.remaining_credits is None


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_errors_are_sanitized(provider, status):
    _, _, reply = provider
    reply({"error": "synthetic-key"}, status)
    result = account.read_balance()
    assert result.status == "unavailable"
    assert "synthetic-key" not in result.model_dump_json()


def test_timeout_and_missing_key(provider):
    settings, client, _ = provider
    client.get.side_effect = httpx.ReadTimeout("synthetic-key")
    assert account.read_balance().status == "unavailable"
    settings.serper_api_key = ""
    assert account.read_balance().status == "not_configured"
    assert client.get.call_count == 1


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.BUYER, UserRole.HEAD,
    UserRole.AUDITOR, UserRole.GUEST, None])
def test_endpoint_access(provider, role):
    _, upstream, _ = provider
    app = FastAPI()
    app.include_router(router)
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role)
    with TestClient(app) as client:
        response = client.get("/settings/integrations/serper/balance")
    assert response.status_code == (401 if role is None else 200 if role == UserRole.ADMIN else 403)
    if role == UserRole.ADMIN:
        assert response.json()["remaining_credits"] == 2500
        assert response.headers["cache-control"] == "no-store"
    else:
        upstream.get.assert_not_called()
