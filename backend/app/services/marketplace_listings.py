"""Продавцы, вычитанные из поисковой выдачи о площадках.

Зачем отдельный модуль. Страницы Echemi нам недоступны: все 29 когда-либо
загруженных вернули HTTP 200 с challenge защитного экрана вместо
содержимого — одинаковые 34218 символов с токенами `_waf_…`. Проверено и
со стенда, и снаружи, так что дело не в адресе и не в заголовках. Обходить
защиту мы не будем, значит читать площадку нечем.

Но Google их страницы проиндексировал, и описания в выдаче устроены
строго единообразно:

    Contact China Manufactory Shandong zhishang chemical Co.,Ltd
    for the product Acetylsalicylic Acid CAS 50-78-2 . Chat now …

Из одной строки достаются имя, страна и роль — причём роль присвоена
площадкой, а не написана компанией о себе. Это не доказательство: продавец
выбирает её сам при регистрации. Но это сторонняя аттестация, которой на
собственных сайтах нет вовсе, и держать её надо отдельно от наших
проверенных доказательств.

Замер по сохранённым выдачам: из 50 ссылок на echemi разбирается 12, что
даёт 10 различных компаний. Стоит это ноль запросов и ноль загрузок —
заголовок и описание приходят вместе с результатом поиска.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Роли в терминах площадки и наши. «Supplier» намеренно ни во что не
# переводится: на Echemi так подписан любой продавец, и роли в этом слове
# столько же, сколько в слове «продаёт».
_ROLE_MAP = {
    "manufactory": "manufacturer",
    "manufacturer": "manufacturer",
    "trader": "trader",
    "distributor": "distributor",
}

_ECHEMI_LISTING_RE = re.compile(
    r"Contact\s+(?P<country>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+"
    r"(?P<role>Manufactory|Manufacturer|Trader|Distributor|Supplier)\s+"
    r"(?P<company>.+?)\s+for\s+the\s+product\b"
)

# Страна в описании написана по-английски, а в реестре мы пишем по-русски.
_COUNTRIES = {
    "china": "Китай",
    "india": "Индия",
    "united kingdom": "Великобритания",
    "united states": "США",
    "usa": "США",
    "germany": "Германия",
    "japan": "Япония",
    "korea": "Корея",
    "south korea": "Корея",
    "singapore": "Сингапур",
    "russia": "Россия",
    "turkey": "Турция",
    "vietnam": "Вьетнам",
    "thailand": "Таиланд",
    "netherlands": "Нидерланды",
    "france": "Франция",
    "italy": "Италия",
    "spain": "Испания",
    "poland": "Польша",
    "brazil": "Бразилия",
}

# Короткое имя после разбора — почти наверняка обрезок, а не компания.
_MIN_COMPANY_LENGTH = 4
_MAX_COMPANY_LENGTH = 120


@dataclass(frozen=True)
class MarketplaceSeller:
    """Продавец, названный площадкой в поисковой выдаче."""

    company: str
    platform: str
    listing_url: str
    # Роль и страна по версии площадки. Не доказательство: продавец
    # указывает их сам при регистрации.
    claimed_role: str | None = None
    country: str | None = None
    # Google обрезает длинные описания, и имя приезжает усечённым:
    # «Hainan Flying International Trade Co., L». Для поиска сайта такое
    # имя годится, для точного сопоставления — нет.
    truncated: bool = False


def _platform_of(url: str) -> str | None:
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").casefold()
    if "echemi.com" in host:
        return "echemi"
    return None


def _looks_truncated(company: str) -> bool:
    """Описание оборвано на полуслове."""
    tail = company.rsplit(" ", 1)[-1].strip(".,")
    if len(tail) <= 1:
        return True
    # «Co., L» — юридическая форма не дописана.
    return bool(re.search(r"\bCo\.?,?\s+L$", company, re.IGNORECASE))


def parse_seller(url: str, title: str, snippet: str) -> MarketplaceSeller | None:
    """Разбирает один результат выдачи в продавца площадки.

    Возвращает None, если ссылка не на площадку или описание устроено
    иначе: у Echemi так выглядят справочные страницы, паспорта
    безопасности и общие каталоги — компании в них не названо.
    """
    platform = _platform_of(url)
    if platform != "echemi":
        return None

    match = _ECHEMI_LISTING_RE.search(snippet or "")
    if match is None:
        return None

    company = " ".join(match.group("company").split()).strip(" .,")
    if not (_MIN_COMPANY_LENGTH <= len(company) <= _MAX_COMPANY_LENGTH):
        return None

    role = _ROLE_MAP.get(match.group("role").casefold())
    country = _COUNTRIES.get(match.group("country").casefold())

    return MarketplaceSeller(
        company=company,
        platform=platform,
        listing_url=url,
        claimed_role=role,
        country=country,
        truncated=_looks_truncated(company),
    )


def collect_sellers(results: list[dict]) -> list[MarketplaceSeller]:
    """Продавцы из пачки результатов поиска, без повторов.

    Один продавец обычно попадается несколькими товарными карточками;
    в реестр он должен уйти одной записью.
    """
    sellers: dict[str, MarketplaceSeller] = {}
    for item in results or []:
        seller = parse_seller(
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
        )
        if seller is None:
            continue
        key = seller.company.casefold()
        current = sellers.get(key)
        # Целое имя лучше обрезанного, роль лучше её отсутствия.
        if current is None or (current.truncated and not seller.truncated):
            sellers[key] = seller
        elif current.claimed_role is None and seller.claimed_role is not None:
            sellers[key] = seller
    return list(sellers.values())
