"""Детерминированная проверка разбора поисковой выдачи без доступа в интернет."""

import httpx
import pytest

from app.connectors import web_search
from app.connectors.web_search import (
    DuckDuckGoHtmlProvider,
    SearchProviderNotConfigured,
    SearchSourceBlocked,
    UnknownSearchProvider,
    available_providers,
    get_search_provider,
    looks_blocked,
    parse_search_results,
    search_web,
)


def test_parse_search_results_extracts_direct_source():
    page = """
    <div class="result">
      <a class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fproduct">
        Example &amp; Chemical
      </a>
      <a class="result__snippet">Official <b>manufacturer</b> product page</a>
    </div>
    """
    results = parse_search_results(page)
    assert results == [
        {
            "title": "Example & Chemical",
            "url": "https://example.com/product",
            "snippet": "Official manufacturer product page",
        }
    ]


def test_parse_search_results_accepts_reordered_attributes_without_snippet():
    page = """
    <div class="result">
      <a href="https://example.com/chemical"
         rel="nofollow"
         class="result__a">Chemical producer</a>
    </div>
    """
    assert parse_search_results(page) == [
        {
            "title": "Chemical producer",
            "url": "https://example.com/chemical",
            "snippet": "",
        }
    ]


def test_parse_search_results_rejects_non_http_links():
    page = """
    <div class="result">
      <a class="result__a" href="javascript:alert(1)">Unsafe result</a>
    </div>
    """
    assert parse_search_results(page) == []


def test_empty_result_page_is_not_treated_as_a_block():
    """Честно пустая выдача сообщает об этом сама и блокировкой не является."""
    page = '<html><body><div class="no-results">No results.</div></body></html>'
    assert looks_blocked(page, 0) is False


def test_page_without_results_and_without_explanation_is_a_block():
    """Ответ без результатов и без сообщения о пустоте — это не выдача."""
    page = "<html><body><h1>Whoa there!</h1><p>Try again later.</p></body></html>"
    assert looks_blocked(page, 0) is True


def test_anti_bot_markers_are_recognised():
    page = "<html><body>Please verify you are human to continue</body></html>"
    assert looks_blocked(page, 0) is True


def test_page_with_results_is_never_a_block():
    assert looks_blocked("<html>anomaly captcha</html>", 3) is False


def test_search_web_raises_on_a_silently_blocked_response(monkeypatch):
    """Заблокированный источник обязан отличаться от «ничего не найдено».

    До этой проверки такой ответ доходил до конвейера как пустой список, и
    запуск завершался успехом с нулём кандидатов.
    """
    deferred: list[tuple[str, float]] = []
    monkeypatch.setattr(
        web_search, "reserve_slot", lambda url, *a, **k: 0.0
    )
    monkeypatch.setattr(
        web_search, "defer_domain", lambda url, delay: deferred.append((url, delay))
    )

    class _BlockedClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url, params=None):
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, request=request, text="<html><body>Whoa there!</body></html>"
            )

    monkeypatch.setattr(web_search.httpx, "Client", _BlockedClient)

    with pytest.raises(SearchSourceBlocked):
        search_web("urea 57-13-6 manufacturer China")
    assert deferred, "заблокировавший источник должен быть отложен для всех процессов"


def test_duckduckgo_http_block_is_a_source_failure(monkeypatch):
    """403 нельзя отдавать как общий HTTP-сбой и повторять весь план с паузами."""
    monkeypatch.setattr(web_search, "reserve_slot", lambda url, *a, **k: 0.0)
    deferred: list[str] = []
    monkeypatch.setattr(
        web_search, "defer_domain", lambda url, delay: deferred.append(url)
    )

    class _BlockedClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params=None):
            return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(web_search.httpx, "Client", _BlockedClient)
    with pytest.raises(SearchSourceBlocked, match="HTTP 403"):
        DuckDuckGoHtmlProvider().search("urea", 8)
    assert deferred


def test_default_provider_is_the_keyless_html_search():
    provider = get_search_provider()
    assert provider.name == "duckduckgo_html"
    assert "duckduckgo_html" in available_providers()


def test_provider_is_selected_by_configuration():
    provider = get_search_provider("duckduckgo_html")
    assert isinstance(provider, DuckDuckGoHtmlProvider)


def test_unknown_provider_fails_loudly_and_lists_the_available_ones():
    """Опечатка в настройке не должна тихо откатываться на прежний источник."""
    with pytest.raises(UnknownSearchProvider) as excinfo:
        get_search_provider("yandex_search")
    assert "duckduckgo_html" in str(excinfo.value)


def test_search_web_delegates_to_the_configured_provider(monkeypatch):
    """Смена источника — это настройка: конвейер её не замечает."""
    calls: list[tuple[str, int]] = []

    class _StubProvider:
        name = "stub"

        def search(self, query, limit=8):
            calls.append((query, limit))
            return [{"title": "t", "url": "https://example.test", "snippet": ""}]

    monkeypatch.setattr(
        web_search, "get_search_provider", lambda name=None: _StubProvider()
    )
    results = search_web("urea manufacturer", 5)
    assert calls == [("urea manufacturer", 5)]
    assert results[0]["url"] == "https://example.test"


def _serper_settings(monkeypatch, key: str = "test-key"):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "serper_api_key", key, raising=False)
    monkeypatch.setattr(settings, "search_provider", "serper", raising=False)
    return settings


def test_serper_requires_a_key_and_says_so(monkeypatch):
    """Провайдер без ключа обязан падать на выборе, а не на первом запросе."""
    _serper_settings(monkeypatch, key="")
    with pytest.raises(SearchProviderNotConfigured) as excinfo:
        get_search_provider("serper")
    assert "SERPER_API_KEY" in str(excinfo.value)


def test_serper_maps_organic_results_to_the_pipeline_shape(monkeypatch):
    _serper_settings(monkeypatch)
    monkeypatch.setattr(web_search, "reserve_slot", lambda url, *a, **k: 0.0)
    captured: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "organic": [
                        {
                            "title": "Hebei Plant",
                            "link": "https://plant.example/urea",
                            "snippet": "Urea CAS 57-13-6",
                        },
                        {"title": "no scheme", "link": "ftp://bad.example"},
                        {
                            "title": "duplicate",
                            "link": "https://plant.example/urea",
                        },
                    ]
                },
            )

    monkeypatch.setattr(web_search.httpx, "Client", _Client)
    results = get_search_provider("serper").search("urea 57-13-6", 8)

    assert results == [
        {
            "title": "Hebei Plant",
            "url": "https://plant.example/urea",
            "snippet": "Urea CAS 57-13-6",
        }
    ], "нехттп-ссылки и дубликаты не должны доходить до конвейера"
    assert captured["headers"]["X-API-KEY"] == "test-key"
    assert captured["json"]["q"] == "urea 57-13-6"


def test_serper_quota_exhaustion_is_a_source_failure(monkeypatch):
    """Исчерпанная квота — отказ источника, а не отсутствие поставщиков."""
    _serper_settings(monkeypatch)
    monkeypatch.setattr(web_search, "reserve_slot", lambda url, *a, **k: 0.0)
    deferred: list = []
    monkeypatch.setattr(
        web_search, "defer_domain", lambda url, delay: deferred.append(url)
    )

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            return httpx.Response(429, request=httpx.Request("POST", url))

    monkeypatch.setattr(web_search.httpx, "Client", _Client)
    with pytest.raises(SearchSourceBlocked):
        get_search_provider("serper").search("urea", 8)
    assert deferred


def test_serper_empty_organic_block_is_a_genuine_empty_result(monkeypatch):
    """У API пустой ответ однозначен и блокировкой не считается."""
    _serper_settings(monkeypatch)
    monkeypatch.setattr(web_search, "reserve_slot", lambda url, *a, **k: 0.0)

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            return httpx.Response(
                200, request=httpx.Request("POST", url), json={"organic": []}
            )

    monkeypatch.setattr(web_search.httpx, "Client", _Client)
    assert get_search_provider("serper").search("несуществующее вещество") == []
