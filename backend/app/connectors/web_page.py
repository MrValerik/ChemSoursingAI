"""Safe, bounded retrieval of primary web pages."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_TEXT = 200_000
MAX_REDIRECTS = 3
_ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


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


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)


def extract_page_text(content: str, content_type: str) -> tuple[str | None, str]:
    if content_type == "text/plain":
        return None, " ".join(content.split())[:MAX_PAGE_TEXT]
    parser = _TextExtractor()
    parser.feed(content)
    title = " ".join(" ".join(parser.title_parts).split()) or None
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return title, html.unescape(text)[:MAX_PAGE_TEXT]


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


def fetch_web_page(
    url: str,
    *,
    max_bytes: int = MAX_PAGE_BYTES,
    resolver: Callable = socket.getaddrinfo,
    transport: httpx.BaseTransport | None = None,
) -> FetchedPage:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChemSourceAI/1.0)"}
    current_url = url
    with httpx.Client(
        timeout=20,
        follow_redirects=False,
        headers=headers,
        transport=transport,
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
                declared_size = response.headers.get("content-length")
                if declared_size and int(declared_size) > max_bytes:
                    raise PageFetchError("Страница превышает допустимый размер")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise PageFetchError("Страница превышает допустимый размер")
                    chunks.append(chunk)
                raw = b"".join(chunks)
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
                )
    raise PageFetchError("Источник не удалось загрузить")
