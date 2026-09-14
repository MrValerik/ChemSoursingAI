"""Exercise the ordinary RFQ queue and automatic-to-manual browser transition."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from app import echemi_worker
from app.connectors import echemi
from app.core.config import Settings
from tests.test_echemi_rfq import rfq
from tests.test_echemi_search import env


@pytest.mark.parametrize("linked,attempts", [(True, 3), (False, 3), (True, 0), (True, 1)])
def test_ordinary_worker_sends_automatic_budget_and_preserves_results(env, monkeypatch, linked, attempts):
    client, _, sessions = env
    if linked:
        with sessions() as db:
            row = rfq(db)
            db.commit()
            rid = row.id
        response = client.post(f"/echemi-searches/rfq/{rid}")
    else:
        response = client.post("/echemi-searches", json={"query": "50-78-2"})
    assert response.status_code in {200, 201}
    sid = response.json()["id"]
    monkeypatch.setattr(echemi, "get_settings", lambda: SimpleNamespace(
        echemi_browser_url="http://synthetic-browser", echemi_captcha_auto_attempts=attempts))
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "results": [{"title": "Synthetic"}],
                                        "diagnostics": {"captcha_probe_attempts": attempts}})

    original = httpx.Client
    monkeypatch.setattr(echemi.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert echemi_worker.run_one()
    assert sent == [{"query": "50-78-2", "search_id": sid,
                     "captcha_probe_attempts": attempts, "captcha_manual_fallback": True}]
    result = client.get(f"/echemi-searches/{sid}").json()
    assert result["status"] == "completed" and result["results"][0]["title"] == "Synthetic"
    assert result["diagnostics"]["captcha_probe_attempts"] == attempts


def test_automatic_budget_defaults_to_three_and_rejects_unbounded_configuration(monkeypatch):
    monkeypatch.delenv("ECHEMI_CAPTCHA_AUTO_ATTEMPTS", raising=False)
    assert Settings(_env_file=None).echemi_captcha_auto_attempts == 3
    for value in [-1, 4]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, echemi_captcha_auto_attempts=value)


@pytest.fixture
def browser(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "echemi-browser"
    monkeypatch.syspath_prepend(str(directory))
    # Playwright is installed in the browser image, not in the backend test runtime.
    # These orchestration tests must fail if they accidentally launch a browser.
    playwright = ModuleType("playwright.async_api")
    playwright.async_playwright = Mock(side_effect=AssertionError("Unexpected live browser"))
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", playwright)
    spec = importlib.util.spec_from_file_location("echemi_browser_auto_search", directory / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fallback", [False, True])
def test_browser_handler_forwards_search_mode_to_collection(browser, monkeypatch, fallback):
    received = []

    async def collect(query, output, attempts, manual_fallback):
        received.append((query, attempts, manual_fallback))
        output.update(status="completed")

    monkeypatch.setattr(browser, "collect", collect)
    request = browser.Search(search_id=7, query="Synthetic", captcha_probe_attempts=3,
                             captcha_manual_fallback=fallback)
    connection = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    response = asyncio.run(browser.search(request, connection))
    assert response["status"] == "completed"
    assert received == [("Synthetic", 3, fallback)]


@pytest.mark.parametrize("result,fallback,manual", [(True, True, False), (False, True, True),
                                                  (False, False, False), ("error", True, True)])
def test_manual_control_starts_only_after_automatic_attempts(browser, monkeypatch, result, fallback, manual):
    async def run():
        calls = []

        async def automatic(*args, **kwargs):
            calls.append("automatic")
            if result == "error":
                raise TimeoutError("private upstream detail")
            return result

        async def human(*args):
            calls.append("manual")
            return True

        monkeypatch.setattr(browser, "needs_verification", AsyncMock(return_value=True))
        monkeypatch.setattr(browser, "wait_for_human", human)
        probe = SimpleNamespace(run=automatic, remaining=0)
        events = []
        answer = await browser.ready(None, None, events, probe=probe, manual_fallback=fallback)
        assert calls == (["automatic", "manual"] if manual else ["automatic"])
        assert answer is (result is True or manual)
        assert any(e["mode"] == "automatic_fallback" for e in events) is manual
        assert "private" not in str(events)
    asyncio.run(run())


def test_accessible_page_does_not_run_captcha_or_open_manual_control(browser, monkeypatch):
    monkeypatch.setattr(browser, "needs_verification", AsyncMock(return_value=False))
    human, automatic = AsyncMock(), AsyncMock()
    monkeypatch.setattr(browser, "wait_for_human", human)
    assert asyncio.run(browser.ready(None, None, [], probe=SimpleNamespace(run=automatic), manual_fallback=True))
    automatic.assert_not_awaited()
    human.assert_not_awaited()


def test_zero_budget_retains_manual_mode_and_probe_cli_keeps_fail_fast_default(browser, monkeypatch):
    monkeypatch.setattr(browser, "needs_verification", AsyncMock(return_value=True))
    human = AsyncMock(return_value=True)
    monkeypatch.setattr(browser, "wait_for_human", human)
    assert asyncio.run(browser.ready(None, None, [], manual_fallback=True))
    human.assert_awaited_once()
    request = browser.Search(search_id=1, query="Synthetic", captcha_probe_attempts=2)
    assert request.captcha_manual_fallback is False
    for value in [-1, 4, True, 1.5]:
        with pytest.raises(ValidationError):
            browser.Search(search_id=1, query="Synthetic", captcha_probe_attempts=value)


def test_progress_distinguishes_automatic_attempt_and_manual_fallback(browser, monkeypatch):
    output = {"status": "running", "results": [], "diagnostics": {"captcha": [
        {"mode": "automatic_probe", "status": "dragging", "attempt": 2, "attempt_limit": 3}]}}
    active = {"id": 7, "waiting": False, "output": output}
    monkeypatch.setattr(browser, "active", active)
    assert "попытка 2 из 3" in asyncio.run(browser.progress(7))["message"]
    assert "message" not in output  # A snapshot must not overwrite collection progress.
    output["diagnostics"]["captcha"].append({"mode": "automatic_fallback", "status": "manual_required"})
    active["waiting"] = True
    message = asyncio.run(browser.progress(7))["message"]
    assert "Автоматическая проверка" in message and "ручная проверка" in message
    assert "товары уже сохранены" not in message
    output["results"] = [{"title": "Synthetic"}]
    assert "товары уже сохранены" in asyncio.run(browser.progress(7))["message"]
