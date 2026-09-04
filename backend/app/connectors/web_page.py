"""Safe, bounded retrieval of primary web pages."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from app.connectors.web_politeness import robots_verdict, wait_for_domain_slot

MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_TEXT = 200_000
MAX_REDIRECTS = 3
_ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

# Запрос только с User-Agent отсекается защитой сайтов: реальный браузер шлёт
# полтора десятка заголовков. Проверено на store.usp.org — 403 против 200.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8,zh-CN;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Медленный сайт поставщика успевает установить соединение, но долго отдаёт
# тело: единый таймаут смешивал эти случаи.
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

# Повтор только для временных сетевых ошибок и только один раз: логический
# отказ (403, 404) повтором не исправить.
_RETRIABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


class PageFetchError(RuntimeError):
    """The page cannot be safely used as a source."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    domain: str
    title: str | None
    content_type: str
    http_status: int
    text: str
    content_hash: str
    # Крупная страница обрезается, а не отбрасывается: для оценки поставщика
    # хватает начала документа.
    truncated: bool = False
    # Ссылки на раздел «контакты». Нужны, когда на самой странице связи нет:
    # адреса в сохранённый текст не попадают, а HTML к тому времени уже
    # разобран и выброшен.
    contact_links: tuple[str, ...] = ()
    # Ссылки на раздел «о компании». Роль поставщика на товарной странице
    # почти никогда не написана, а на странице о себе — написана.
    profile_links: tuple[str, ...] = ()


# Поля schema.org, которые несут смысл для карточки поставщика. Остальное
# в разметке — навигация, хлебные крошки и счётчики.
# Типы schema.org, описывающие сам товар или его продавца. Всё остальное в
# разметке — навигация и разделы сайта.
_JSONLD_TYPES = frozenset(
    {
        "product",
        "productmodel",
        "chemicalsubstance",
        "offer",
        "organization",
        "corporation",
        "manufacturer",
        "propertyvalue",
        "quantitativevalue",
    }
)

_JSONLD_FIELDS = (
    ("name", "Название"),
    ("description", "Описание"),
    ("sku", "Артикул"),
    ("productID", "Идентификатор"),
    ("brand", "Бренд"),
    ("manufacturer", "Изготовитель"),
    ("category", "Категория"),
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.jsonld_parts: list[str] = []
        self._ignored_depth = 0
        self._in_title = False
        self._in_jsonld = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            if tag == "script":
                kind = next(
                    (v or "" for k, v in attrs if (k or "").casefold() == "type"),
                    "",
                )
                # Разметка schema.org лежит внутри script, поэтому вместе со
                # скриптами терялась. На карточках товара там обычно самое
                # точное описание продукта на всей странице.
                self._in_jsonld = "ld+json" in kind.casefold()
        elif tag == "title":
            self._in_title = True
        elif tag in {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            # Карточки товара часто используют label/select или dt/dd вместо
            # таблицы. Границы не дают «Pack Size» и вариантам склеиться.
            "label",
            "select",
            "option",
            "dt",
            "dd",
        }:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            # Без разделителя соседние ячейки склеиваются: в замере на бетаине
            # получалось «Origin : ChinaCAS Number : 107-43-7», где два разных
            # поля спецификации выглядят одним значением.
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            if tag == "script":
                self._in_jsonld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self.jsonld_parts.append(data)
            return
        if self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)


def _render_jsonld(blocks: list[str]) -> list[str]:
    """Превращает schema.org-разметку в строки «поле: значение».

    Строки попадают в тот же текст страницы, что и остальное содержимое,
    поэтому цитата из них проходит детерминированную проверку наравне с
    цитатой из видимой части. Отдельным полем их держать нельзя: проверка
    ищет цитату в сохранённом тексте.
    """
    import json

    lines: list[str] = []
    seen: set[str] = set()

    def add_line(line: str) -> None:
        normalized = " ".join(line.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            lines.append(normalized)

    def scalar(value) -> str | None:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            rendered = " ".join(str(value).split())
            return rendered or None
        return None

    def walk(node, relation: str | None = None) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, relation)
            return
        if not isinstance(node, dict):
            return
        # Разметка описывает не только товар: там же лежат хлебные крошки,
        # разделы сайта и виджеты. На chemicalbook так в подсветку попало
        # «Название: CAS DataBase List» — имя раздела, а не вещества.
        kinds = node.get("@type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(
            isinstance(kind, str) and kind.casefold() in _JSONLD_TYPES
            for kind in kinds
        ):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value, key)
            return
        normalized_kinds = {
            kind.casefold() for kind in kinds if isinstance(kind, str)
        }
        if normalized_kinds & {"propertyvalue", "quantitativevalue"}:
            label = scalar(node.get("name")) or scalar(node.get("propertyID"))
            if label is None and relation not in {None, "additionalProperty"}:
                label = " ".join(
                    re.sub(r"(?<!^)(?=[A-Z])", " ", relation).split()
                )
            value = scalar(node.get("value"))
            if value is None:
                minimum = scalar(node.get("minValue"))
                maximum = scalar(node.get("maxValue"))
                if minimum and maximum:
                    value = f"{minimum}-{maximum}"
                elif minimum:
                    value = f">= {minimum}"
                elif maximum:
                    value = f"<= {maximum}"
            unit = scalar(node.get("unitText")) or scalar(node.get("unitCode"))
            if label and value:
                add_line(f"{label}: {value}{f' {unit}' if unit else ''}")
        for key, label in _JSONLD_FIELDS:
            value = node.get(key)
            if isinstance(value, dict):
                value = value.get("name")
            if isinstance(value, str) and value.strip():
                add_line(f"{label}: {value}")
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, key)

    for block in blocks:
        text = block.strip()
        if not text:
            continue
        try:
            walk(json.loads(text))
        except (ValueError, RecursionError):
            # Разметка бывает битой; это не повод терять остальную страницу.
            continue
    return lines


def _without_nul(value: str) -> str:
    """Убирает нулевые байты: PostgreSQL их в текстовом поле не хранит.

    Страница с ``\\x00`` в разметке роняла весь прогон при сохранении:
    «PostgreSQL text fields cannot contain NUL (0x00) bytes». Два прогона
    из двадцати одного в замере по эталону погибли именно так.
    """
    return value.replace("\x00", "") if value else value


def extract_page_text(content: str, content_type: str) -> tuple[str | None, str]:
    if content_type == "text/plain":
        return None, _without_nul(" ".join(content.split())[:MAX_PAGE_TEXT])
    parser = _TextExtractor()
    parser.feed(content)
    title = " ".join(" ".join(parser.title_parts).split()) or None
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    body = [line for line in lines if line]
    # Разметка идёт первой: она компактна и описывает товар точнее, чем
    # окружающая её вёрстка, а до конца страницы обрезка может не дойти.
    text = "\n".join(_render_jsonld(parser.jsonld_parts) + body)
    return _without_nul(title), _without_nul(html.unescape(text)[:MAX_PAGE_TEXT])


# Адреса и подписи ссылок, за которыми лежит страница контактов.
_CONTACT_HREF_RE = re.compile(
    r"(contact|contacts|contact-us|contactus|about-us|aboutus|reach-us"
    r"|lianxi|contactos|kontakt)", re.IGNORECASE
)
_CONTACT_TEXT_RE = re.compile(
    r"(contact\s*us|contact|get\s+in\s+touch|reach\s+us|联系我们|联系方式"
    r"|contactez|kontakt)", re.IGNORECASE
)
# Ссылки на разделы, которые лишь похожи на контактные, но ими не являются.
_CONTACT_SKIP_RE = re.compile(
    r"(mailto:|tel:|javascript:|#|/news/|/blog/|/product)", re.IGNORECASE
)
_MAX_CONTACT_LINKS = 3


def find_contact_links(content: str, base_url: str) -> tuple[str, ...]:
    """Ссылки на страницу контактов, вытащенные из разметки.

    Нужны потому, что связь есть не на каждой товарной странице: замер по
    136 сохранённым карточкам дал контакт у 92, а ссылку на раздел
    «контакты» — у 125. Одна догрузка по такой ссылке закрывает
    большинство оставшихся, и это надёжнее, чем угадывать адрес вида
    «/contact.html»: у половины китайских сайтов он другой.

    Ссылки берутся из HTML, пока он ещё есть: в сохранённый текст
    страницы адреса не попадают.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(content)
    except Exception:  # разметка бывает битой, и это не повод падать
        pass
    found: list[str] = []
    for href, label in parser.links:
        if _CONTACT_SKIP_RE.search(href):
            continue
        if not (_CONTACT_HREF_RE.search(href) or _CONTACT_TEXT_RE.search(label)):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if absolute == base_url or absolute in found:
            continue
        found.append(absolute)
        if len(found) >= _MAX_CONTACT_LINKS:
            break
    return tuple(found)


# Адреса и подписи ссылок на раздел «о компании». Отдельно от контактных:
# там ищут связь, здесь — кто эта компания. Пересечение намеренное, у
# половины сайтов это один и тот же раздел.
_PROFILE_HREF_RE = re.compile(
    r"(about|about-us|aboutus|about_us|company|company-profile|profile"
    r"|introduction|gongsi|jianjie|guanyu|o-kompanii|ueber-uns)",
    re.IGNORECASE,
)
_PROFILE_TEXT_RE = re.compile(
    r"(about\s*us|about\s+the\s+company|company\s+profile|who\s+we\s+are"
    r"|our\s+company|关于我们|公司简介|企业简介|公司概况|о\s+компании)",
    re.IGNORECASE,
)
# Разделы, которые лишь похожи на рассказ о компании. «about-shipping» и
# новости о компании роли не доказывают, а загрузку тратят.
_PROFILE_SKIP_RE = re.compile(
    r"(mailto:|tel:|javascript:|#"
    r"|[-/_](?:news|blog|article|product|shipping|delivery|payment|privacy"
    r"|policy|faq|job|career|vacanc))",
    re.IGNORECASE,
)
_MAX_PROFILE_LINKS = 2


def find_profile_links(content: str, base_url: str) -> tuple[str, ...]:
    """Ссылки на раздел «о компании», вытащенные из разметки.

    Зачем. Замер по 45 сохранённым страницам прогонов 328–336: утверждения
    о роли нет на 37 из них ни в одном месте текста. Слово manufacturer
    стоит на 37 страницах, но это вывеска «manufacturer & supplier», и
    доказательством она справедливо не считается. Роль пишут на странице о
    себе: по 19 открывшимся разделам «о компании» штатные читатели фактов
    дали производственную площадку у 4 и офисный адрес у 3.

    Ссылки берутся из разметки, а не угадываются по адресу: угадывание по
    /about, /about-us и ещё трём путям открыло раздел лишь у 19 доменов
    из 27.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(content)
    except Exception:  # разметка бывает битой, и это не повод падать
        pass
    found: list[str] = []
    for href, label in parser.links:
        if _PROFILE_SKIP_RE.search(href):
            continue
        if not (_PROFILE_HREF_RE.search(href) or _PROFILE_TEXT_RE.search(label)):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if absolute == base_url or absolute in found:
            continue
        found.append(absolute)
        if len(found) >= _MAX_PROFILE_LINKS:
            break
    return tuple(found)


class _LinkExtractor(HTMLParser):
    """Пары «адрес ссылки, её подпись»."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        self._href = href.strip()
        self._label = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        label = " ".join("".join(self._label).split())
        if self._href:
            self.links.append((self._href, label))
        self._href = None
        self._label = []


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_public_url(
    url: str,
    *,
    resolver: Callable = socket.getaddrinfo,
) -> str:
    parsed = urlparse(url)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise PageFetchError("Источник должен использовать http или https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise PageFetchError("Некорректный адрес источника")
    try:
        direct_address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        direct_address = None
    if direct_address is not None and not _is_public_address(str(direct_address)):
        raise PageFetchError("Доступ к локальным и частным адресам запрещён")
    try:
        addresses = {
            item[4][0]
            for item in resolver(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme.casefold() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise PageFetchError(f"Не удалось разрешить домен: {exc}") from exc
    if not addresses or any(not _is_public_address(value) for value in addresses):
        raise PageFetchError("Доступ к локальным и частным адресам запрещён")
    return parsed.hostname.casefold()


def _fetch_once(
    url: str,
    *,
    max_bytes: int,
    resolver: Callable,
    transport: httpx.BaseTransport | None,
) -> FetchedPage:
    current_url = url
    with httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers=BROWSER_HEADERS,
        transport=transport,
        http2=transport is None,
    ) as client:
        for redirect_index in range(MAX_REDIRECTS + 1):
            domain = validate_public_url(current_url, resolver=resolver)
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    if redirect_index == MAX_REDIRECTS:
                        raise PageFetchError("Слишком много перенаправлений")
                    location = response.headers.get("location")
                    if not location:
                        raise PageFetchError("Перенаправление без адреса")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .casefold()
                )
                if content_type not in _ALLOWED_CONTENT_TYPES:
                    raise PageFetchError(
                        f"Неподдерживаемый тип источника: {content_type or 'не указан'}"
                    )

                chunks: list[bytes] = []
                total = 0
                truncated = False
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= max_bytes:
                        # Каталоги поставщиков бывают многомегабайтными;
                        # для оценки достаточно начала страницы.
                        truncated = True
                        break
                raw = b"".join(chunks)[:max_bytes]
                encoding = response.encoding or "utf-8"
                content = raw.decode(encoding, errors="replace")
                title, text = extract_page_text(content, content_type)
                if not text:
                    raise PageFetchError("На странице не найден текст")
                return FetchedPage(
                    url=url,
                    final_url=str(response.url),
                    domain=domain,
                    title=title,
                    content_type=content_type,
                    http_status=response.status_code,
                    text=text,
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    truncated=truncated,
                    contact_links=(
                        find_contact_links(content, str(response.url))
                        if content_type != "text/plain"
                        else ()
                    ),
                    profile_links=(
                        find_profile_links(content, str(response.url))
                        if content_type != "text/plain"
                        else ()
                    ),
                )
    raise PageFetchError("Источник не удалось загрузить")


def fetch_web_page(
    url: str,
    *,
    max_bytes: int = MAX_PAGE_BYTES,
    resolver: Callable = socket.getaddrinfo,
    transport: httpx.BaseTransport | None = None,
    respect_robots: bool = True,
) -> FetchedPage:
    """Загружает страницу с браузерными заголовками и одним повтором.

    Повтор выполняется только для временных сетевых ошибок: логический отказ
    сервера (403, 404) повтором не исправить, а лишняя попытка тратит бюджет
    этапа и нагружает чужой сайт.
    """
    if respect_robots and transport is None:
        allowed, crawl_delay = robots_verdict(url, BROWSER_HEADERS["User-Agent"])
        if not allowed:
            raise PageFetchError("Robots.txt сайта запрещает загрузку страницы")
        wait_for_domain_slot(url, crawl_delay)
    try:
        try:
            page = _fetch_once(
                url, max_bytes=max_bytes, resolver=resolver, transport=transport
            )
        except _RETRIABLE_ERRORS:
            page = _fetch_once(
                url, max_bytes=max_bytes, resolver=resolver, transport=transport
            )
    except PageFetchError:
        # Текста нет вовсе — единственный случай, ради которого стоит
        # поднимать браузер. Прочие отказы (403, PDF, запрет robots.txt)
        # им не лечатся, и пробовать их незачем.
        rendered = _render_fallback(url, transport=transport)
        if rendered is not None:
            return rendered
        raise
    if len(page.text or "") >= _render_min_chars():
        return page
    # Текст есть, но его столько же, сколько в шапке меню. Отрисовка либо
    # даст содержимое, либо вернёт то же самое — тогда остаётся исходное.
    rendered = _render_fallback(url, transport=transport)
    if rendered is not None and len(rendered.text or "") > len(page.text or ""):
        return rendered
    return page


def _render_min_chars() -> int:
    from app.core.config import get_settings

    return get_settings().page_render_min_chars


def _render_fallback(
    url: str, *, transport: httpx.BaseTransport | None = None
) -> FetchedPage | None:
    """Открывает страницу браузером через службу renderer.

    Зачем. Часть сайтов присылает не страницу, а программу: замер
    3 сентября 2026 по пяти адресам с ошибкой «на странице не найден текст»
    дал ответы 200 длиной 151–985 байт из одного упакованного скрипта, без
    единой картинки. HTTP-клиент такую страницу прочитать не может: текста
    в ответе нет, он появляется только после исполнения скрипта.

    Отрисовка необязательна. Пустая настройка, недоступная служба или её
    ошибка — не отказ загрузки: карточка остаётся такой же, какой была бы
    без этого пути. Проверку robots.txt делает вызывающая сторона выше, и
    сюда управление доходит только после разрешения.

    В тестах с подставленным транспортом отрисовка не вызывается: они
    описывают разбор ответа, а не поход в чужую службу.
    """
    if transport is not None:
        return None
    from app.core.config import get_settings

    settings = get_settings()
    service = (settings.page_render_url or "").strip().rstrip("/")
    if not service:
        return None
    try:
        response = httpx.post(
            f"{service}/render",
            json={"url": url, "timeout_ms": settings.page_render_timeout_s * 1000},
            timeout=settings.page_render_timeout_s + 5,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    text = _without_nul(" \n".join((data.get("text") or "").split("\n")))
    text = text[:MAX_PAGE_TEXT]
    if not text.strip():
        return None
    final_url = str(data.get("final_url") or url)
    return FetchedPage(
        url=url,
        final_url=final_url,
        domain=urlparse(final_url).netloc.casefold(),
        title=(data.get("title") or None),
        content_type="text/html",
        http_status=data.get("http_status"),
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        # Ссылки на разделы вытаскиваются из разметки, а её здесь нет:
        # служба отдаёт готовый текст. Раздел «о компании» по такой
        # странице просто не догружается.
    )
