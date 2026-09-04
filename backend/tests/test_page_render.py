"""Отрисовка страницы браузером как запасной путь загрузки.

Замер 3 сентября 2026. Из 905 загрузок прогонов 200–351 открылось 703, а из
открывшихся 59 дали меньше полутора тысяч символов. Пять адресов, на которых
загрузчик сообщал «на странице не найден текст», проверены поимённо: все пять
ответили кодом 200 длиной 151–985 байт, состоящим из одного упакованного
скрипта, без единой картинки и без единого символа текста.

Дело не в кодировке (проверены utf-8 и gb18030, битых символов ноль) и не в
том, что текст нарисован изображением. Сервер присылает программу, а не
страницу: HTTP-клиент скачивает байты и разбирает HTML, браузер вдобавок
исполняет скрипт, и содержимое появляется только после этого.

Поэтому запасной путь отдаёт текст, а не снимок экрана: система доказательств
сличает цитату с сохранённым текстом дословно, и подменять его картинкой
нельзя.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_page_render.db")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.connectors import web_page  # noqa: E402
from app.connectors.web_page import PageFetchError, fetch_web_page  # noqa: E402
from app.core.config import get_settings  # noqa: E402

# Ровно то, что присылает chemdrug.com: скрипт и ничего больше.
SCRIPT_ONLY = (
    '<html><head><meta charset="gb2312"></head><body>'
    '<script language="javascript">eval(function(p,a,c,k,e,d){return p}'
    "('0.1(\\'x\\')',2,2,'a|b'.split('|'),0,{}))</script>"
    "</body></html>"
)
RENDERED_TEXT = (
    "L-乳酸薄荷酯\nCAS号 61597-98-6\n"
    "本公司专业供应各种化工中间体，品种达数万种。\n"
    "生产厂家：重庆某某生物科技有限公司\n"
) * 4


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _transportless_html(html: str):
    """Отдаёт страницу без подставленного транспорта.

    Отрисовка намеренно не вызывается, когда транспорт подставлен: такие
    тесты описывают разбор ответа, а не поход в чужую службу. Здесь нужен
    именно путь без транспорта, поэтому перехватывается сама загрузка.
    """

    def _fetch_once(url, *, max_bytes, resolver, transport):
        title, text = web_page.extract_page_text(html, "text/html")
        if not text:
            raise PageFetchError("На странице не найден текст")
        return web_page.FetchedPage(
            url=url,
            final_url=url,
            domain="example.cn",
            title=title,
            content_type="text/html",
            http_status=200,
            text=text,
            content_hash="x",
        )

    return _fetch_once


def _render_service(monkeypatch, payload: dict, *, calls: list):
    def _post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        return httpx.Response(
            200, json=payload, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(web_page.httpx, "post", _post)


def test_страница_из_одного_скрипта_читается_браузером(monkeypatch):
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    monkeypatch.setattr(web_page, "_fetch_once", _transportless_html(SCRIPT_ONLY))
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(web_page, "wait_for_domain_slot", lambda *a, **k: None)
    calls: list = []
    _render_service(
        monkeypatch,
        {
            "final_url": "https://example.cn/item",
            "title": "L-乳酸薄荷酯",
            "text": RENDERED_TEXT,
            "http_status": 200,
        },
        calls=calls,
    )

    page = fetch_web_page("https://example.cn/item")

    assert "61597-98-6" in page.text
    assert page.title == "L-乳酸薄荷酯"
    assert calls and calls[0][0] == "http://renderer:8080/render"
    assert calls[0][1]["url"] == "https://example.cn/item"


def test_без_настройки_отрисовки_поведение_прежнее(monkeypatch):
    monkeypatch.setenv("PAGE_RENDER_URL", "")
    monkeypatch.setattr(web_page, "_fetch_once", _transportless_html(SCRIPT_ONLY))
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(web_page, "wait_for_domain_slot", lambda *a, **k: None)

    with pytest.raises(PageFetchError, match="не найден текст"):
        fetch_web_page("https://example.cn/item")


def test_недоступная_служба_отрисовки_не_ломает_загрузку(monkeypatch):
    """Отрисовка необязательна: её отказ оставляет прежний исход."""
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    monkeypatch.setattr(web_page, "_fetch_once", _transportless_html(SCRIPT_ONLY))
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(web_page, "wait_for_domain_slot", lambda *a, **k: None)

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("служба не поднята")

    monkeypatch.setattr(web_page.httpx, "post", _boom)

    with pytest.raises(PageFetchError, match="не найден текст"):
        fetch_web_page("https://example.cn/item")


def test_запрет_robots_до_отрисовки_не_доходит(monkeypatch):
    """Браузер не обходит запрет: проверка robots.txt стоит раньше."""
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (False, 0.0))
    calls: list = []
    _render_service(monkeypatch, {"final_url": "x", "text": "y"}, calls=calls)

    with pytest.raises(PageFetchError, match="Robots.txt"):
        fetch_web_page("https://example.cn/item")
    assert calls == []


def test_короткая_страница_догружается_браузером(monkeypatch):
    """Текст есть, но его столько же, сколько в шапке меню."""
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    monkeypatch.setenv("PAGE_RENDER_MIN_CHARS", "400")
    monkeypatch.setattr(
        web_page,
        "_fetch_once",
        _transportless_html("<html><body><p>Главная Товары Контакты</p></body></html>"),
    )
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(web_page, "wait_for_domain_slot", lambda *a, **k: None)
    _render_service(
        monkeypatch,
        {
            "final_url": "https://example.cn/item",
            "title": "Товар",
            "text": RENDERED_TEXT,
            "http_status": 200,
        },
        calls=[],
    )

    page = fetch_web_page("https://example.cn/item")
    # Отрисовка принимается потому, что даёт больше исходного, а не потому,
    # что перевалила порог: китайский текст короче того же по смыслу
    # английского, и мерить содержательность длиной нельзя.
    assert "61597-98-6" in page.text
    assert "Главная Товары Контакты" not in page.text


def test_отрисовка_короче_исходного_текста_не_принимается(monkeypatch):
    """Служба вернула меньше, чем было, — оставляем исходную страницу."""
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    monkeypatch.setenv("PAGE_RENDER_MIN_CHARS", "100000")
    long_html = "<html><body><p>" + ("Товарная страница поставщика. " * 40) + "</p></body></html>"
    monkeypatch.setattr(web_page, "_fetch_once", _transportless_html(long_html))
    monkeypatch.setattr(web_page, "robots_verdict", lambda *a, **k: (True, 0.0))
    monkeypatch.setattr(web_page, "wait_for_domain_slot", lambda *a, **k: None)
    _render_service(
        monkeypatch,
        {"final_url": "https://example.cn/item", "title": "x", "text": "коротко"},
        calls=[],
    )

    page = fetch_web_page("https://example.cn/item")
    assert "Товарная страница поставщика" in page.text


def test_подставленный_транспорт_отрисовку_не_зовёт(monkeypatch):
    """Тесты разбора ответа в чужую службу не ходят."""
    monkeypatch.setenv("PAGE_RENDER_URL", "http://renderer:8080")
    calls: list = []
    _render_service(monkeypatch, {"final_url": "x", "text": "y"}, calls=calls)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text=SCRIPT_ONLY
        )
    )

    with pytest.raises(PageFetchError):
        fetch_web_page("https://example.cn/item", transport=transport)
    assert calls == []
