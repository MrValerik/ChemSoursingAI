"""Детерминированная проверка разбора поисковой выдачи без доступа в интернет."""

import httpx
import pytest

from app.connectors import web_search
from app.connectors.web_search import (
    SearchSourceBlocked,
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
