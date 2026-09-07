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

# Второй шаблон — заголовок товарной карточки, а не описание. Проверено на
# живой выдаче 21 августа: страницы `/produce/` приходят из индекса Google
# заголовком вида
#
#     Buy Factory Price High Quality Aspirin … CAS 50-78-2
#     from SHANDONG LOOK CHEMICAL CO.,LTD - ECHEMI
#
# Имя продавца стоит между «from» и хвостом «- ECHEMI». Страны и роли в
# заголовке нет — площадка их сюда не пишет, — но само имя приезжает
# чистым и без единой загрузки echemi. Справочные заголовки («… Market
# Analysis - ECHEMI - ECHEMI.com», «… Formula - ECHEMI», «… SDS … - ECHEMI»)
# слова «from» перед хвостом не имеют и сюда не попадают.
_ECHEMI_TITLE_RE = re.compile(
    r"\bfrom\s+(?P<company>.+?)\s*[-–—]\s*ECHEMI\s*$",
    re.IGNORECASE,
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


def _is_echemi_product_listing(url: str) -> bool:
    """Товарная карточка `/produce/…` — на ней назван конкретный продавец.

    Отделяет её от справочных разделов того же домена: `/products/` и
    `/productsInformation/` — карточки вещества и рыночная аналитика,
    `/sds/` — паспорта безопасности. Компании там нет, и заголовочный
    шаблон к ним применять нельзя.
    """
    path = urlparse(url if "//" in url else f"//{url}").path.casefold()
    return path.startswith("/produce/")


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

    # Основной путь — описание «Contact <страна> <роль> <компания> for the
    # product …». Даёт заодно страну и заявленную роль.
    match = _ECHEMI_LISTING_RE.search(snippet or "")
    if match is not None:
        company = " ".join(match.group("company").split()).strip(" .,")
        if not (_MIN_COMPANY_LENGTH <= len(company) <= _MAX_COMPANY_LENGTH):
            return None
        return MarketplaceSeller(
            company=company,
            platform=platform,
            listing_url=url,
            claimed_role=_ROLE_MAP.get(match.group("role").casefold()),
            country=_COUNTRIES.get(match.group("country").casefold()),
            truncated=_looks_truncated(company),
        )

    # Запасной путь — заголовок товарной карточки «… from <компания> -
    # ECHEMI». Только на страницах `/produce/`: там назван продавец. Роли и
    # страны в заголовке нет, поэтому оба поля остаются пустыми — выдумывать
    # их не из чего.
    if _is_echemi_product_listing(url):
        title_match = _ECHEMI_TITLE_RE.search(title or "")
        if title_match is not None:
            company = " ".join(title_match.group("company").split()).strip(" .,")
            if _MIN_COMPANY_LENGTH <= len(company) <= _MAX_COMPANY_LENGTH:
                return MarketplaceSeller(
                    company=company,
                    platform=platform,
                    listing_url=url,
                    claimed_role=None,
                    country=None,
                    truncated=_looks_truncated(company),
                )

    return None


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


# --- поиск собственного сайта продавца (ADR-0001, вариант B) ---

# Домен-метка против слов названия. Совпадение здесь — сильный признак:
# «zhishangchem.com» у «Shandong zhishang chemical» принадлежит именно ей.
#
# Замер 07.09.2026 на шести компаниях: сверка по одному заголовку страницы
# пропустила importgenius.cn — агрегатор судовых записей, у которого имя
# компании стоит в заголовке. Страница О компании выглядит как страница
# КОМПАНИИ, и различить их можно только по домену.
#
# Цена правила известна: сайт под торговой маркой оно отвергает. Проверено
# там же — senwayer.com принадлежит Dingwang Technology (Wuhan), но её
# имени в домене нет. Правило оставлено строгим сознательно: по ложному
# контакту уходит письмо чужой компании, а пропуск закупщик закроет
# руками.
_GENERIC_DOMAIN_WORDS = frozenset(
    {
        "chemical", "chemicals", "chem", "group", "china", "cn", "com",
        "trade", "trading", "biotech", "bio", "tech", "technology", "inc",
        "ltd", "co", "industry", "industrial", "import", "export", "global",
        "international", "shop", "store", "mall", "market", "info", "online",
    }
)

# Минимальная длина куска названия, по которому домен признаётся своим.
# Короткое совпадение («bio», «tech») даёт ложные срабатывания почти на
# любой химической компании.
_MIN_DOMAIN_TOKEN = 4


def _company_words(company: str) -> list[str]:
    """Значимые слова названия для сверки с доменом."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (company or "").casefold())
    return [
        word
        for word in cleaned.split()
        if len(word) >= _MIN_DOMAIN_TOKEN and word not in _GENERIC_DOMAIN_WORDS
    ]


def site_belongs_to_company(company: str, url: str) -> bool:
    """Домен принадлежит компании, а не рассказывает о ней.

    Сравнивается метка домена со значимыми словами названия. Это грубее
    сверки по заголовку, но именно грубость здесь и нужна: агрегатор,
    каталог и справочник называют компанию в заголовке так же охотно, как
    её собственный сайт, а вот в домен её имя ставит только она сама.
    """
    # Импорт внутри функции: модуль реестра тянет за собой модели и
    # сессию, а разбор выдачи обязан оставаться без базы.
    from app.services.intermediaries import domain_label

    label = re.sub(r"[^a-z0-9]+", "", domain_label(url).casefold())
    if len(label) < _MIN_DOMAIN_TOKEN:
        return False
    return any(word in label or label in word for word in _company_words(company))
