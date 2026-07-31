"""Вежливый обход: robots.txt и пауза между запросами к одному домену."""

import time

import httpx
import pytest

from app.connectors import web_politeness
from app.connectors.web_politeness import (
    reset_politeness_state,
    robots_verdict,
    wait_for_domain_slot,
)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"


@pytest.fixture(autouse=True)
def _clean_state():
    reset_politeness_state()
    yield
    reset_politeness_state()


def _robots_response(monkeypatch, body: str | None, status: int = 200):
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            status, text=body or "", request=request
        )

    monkeypatch.setattr(web_politeness.httpx, "get", fake_get)


def test_disallowed_path_is_refused(monkeypatch):
    _robots_response(
        monkeypatch,
        "User-agent: *\nDisallow: /searchGoods/\nDisallow: /front/\n",
    )
    allowed, _ = robots_verdict("https://www.echemi.com/searchGoods/x", _UA)
    assert allowed is False
    allowed, _ = robots_verdict("https://www.echemi.com/produce/aspirin.html", _UA)
    assert allowed is True


def test_missing_robots_is_not_treated_as_a_ban(monkeypatch):
    # Защита от ботов часто закрывает и сам robots.txt: считать это запретом
    # значило бы отказаться от всего домена.
    _robots_response(monkeypatch, None, status=403)
    allowed, delay = robots_verdict("https://store.usp.org/product/1", _UA)
    assert allowed is True
    assert delay is None


def test_unreachable_robots_is_not_treated_as_a_ban(monkeypatch):
    def failing_get(url, **kwargs):
        raise httpx.ConnectTimeout("no route", request=httpx.Request("GET", url))

    monkeypatch.setattr(web_politeness.httpx, "get", failing_get)
    allowed, _ = robots_verdict("https://unreachable.example/p", _UA)
    assert allowed is True


def test_crawl_delay_is_reported(monkeypatch):
    _robots_response(monkeypatch, "User-agent: *\nCrawl-delay: 2\nDisallow:\n")
    allowed, delay = robots_verdict("https://slow.example/p", _UA)
    assert allowed is True
    assert delay == 2.0


def test_robots_is_fetched_once_per_origin(monkeypatch):
    calls = {"count": 0}

    def counting_get(url, **kwargs):
        calls["count"] += 1
        request = httpx.Request("GET", url)
        return httpx.Response(200, text="User-agent: *\nDisallow:\n", request=request)

    monkeypatch.setattr(web_politeness.httpx, "get", counting_get)
    for _ in range(3):
        robots_verdict("https://cached.example/page", _UA)
    assert calls["count"] == 1


def test_second_request_to_same_domain_waits():
    started = time.monotonic()
    wait_for_domain_slot("https://polite.example/a", 0.2)
    wait_for_domain_slot("https://polite.example/b", 0.2)
    assert time.monotonic() - started >= 0.2


def test_different_domains_do_not_block_each_other():
    started = time.monotonic()
    wait_for_domain_slot("https://one.example/a", 5.0)
    wait_for_domain_slot("https://two.example/a", 5.0)
    assert time.monotonic() - started < 1.0
