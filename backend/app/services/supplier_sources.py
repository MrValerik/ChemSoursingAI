"""Search plans and source classification for supplier sourcing."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

SourceKind = Literal["echemi", "india_registry", "india_web", "web"]

_INDIA_REGISTRY_DOMAINS = (
    "chemexcil.in",
    "cdsco.gov.in",
    "pharmexcil.com",
    "parivesh.nic.in",
    "dgft.gov.in",
)


# Домены, которые не являются искомыми производителями: дистрибьюторы
# реактивов, продавцы фармакопейных стандартов, энциклопедии и маркетплейсы
# общего назначения. Их страницы либо закрыты защитой от ботов, либо всё равно
# отбраковываются на квалификации как посредники, поэтому загружать их — тратить
# бюджет этапа и место в короткой выдаче.
NON_MANUFACTURER_DOMAINS = (
    # Дистрибьюторы реактивов и лабораторных химикатов.
    "sigmaaldrich.com",
    "merckmillipore.com",
    "merckgroup.com",
    "thermofisher.com",
    "fishersci.com",
    "fishersci.co.uk",
    "vwr.com",
    "avantorsciences.com",
    "tcichemicals.com",
    "alfa.com",
    "acros.com",
    "carlroth.com",
    "honeywell.com",
    "spectrumchemical.com",
    "caymanchem.com",
    "santacruzbio.com",
    "abcam.com",
    "bocsci.com",
    # Продавцы фармакопейных стандартов.
    "usp.org",
    "edqm.eu",
    "nist.gov",
    "lgcstandards.com",
    # Справочники, энциклопедии и базы данных.
    "wikipedia.org",
    "pubchem.ncbi.nlm.nih.gov",
    "chemspider.com",
    "drugbank.com",
    "cas.org",
    "commonchemistry.cas.org",
    "guidechem.com",
    "chemicalbook.com",
    "chemnet.com",
    "lookchem.com",
    "molbase.com",
    "chemblink.com",
    "chemeo.com",
    # Маркетплейсы общего назначения и агрегаторы объявлений.
    "alibaba.com",
    "aliexpress.com",
    "made-in-china.com",
    "indiamart.com",
    "tradeindia.com",
    "exportersindia.com",
    "ec21.com",
    "tradekey.com",
    "amazon.com",
    "ebay.com",
    # Соцсети и агрегаторы вакансий/компаний.
    "linkedin.com",
    "facebook.com",
    "youtube.com",
    "crunchbase.com",
    "bloomberg.com",
    "zoominfo.com",
)


def is_non_manufacturer_domain(url: str) -> bool:
    """Домен заведомо не является производителем искомого вещества."""
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not hostname:
        return False
    return any(
        _host_matches(hostname, domain) for domain in NON_MANUFACTURER_DOMAINS
    )


def is_china(country: str | None) -> bool:
    return (country or "").strip().casefold() in {
        "china",
        "китай",
        "cn",
        "prc",
        "中国",
    }


def is_india(country: str | None) -> bool:
    return (country or "").strip().casefold() in {
        "india",
        "индия",
        "in",
        "bharat",
        "भारत",
    }


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def source_kind(url: str) -> SourceKind:
    """Classify a URL without treating a marketplace claim as verification."""
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if _host_matches(hostname, "echemi.com"):
        return "echemi"
    if any(_host_matches(hostname, domain) for domain in _INDIA_REGISTRY_DOMAINS):
        return "india_registry"
    if hostname.endswith(".in"):
        return "india_web"
    return "web"


def source_priority(kind: SourceKind, country: str | None) -> int:
    """Отраслевой реестр важнее всего; торговая площадка не имеет преимущества.

    Раньше площадка получала восемь баллов и обгоняла сайты самих компаний.
    Для поиска изготовителя это ровно обратный порядок: карточка на витрине
    не подтверждает производство, а реестр — подтверждает.
    """
    if is_india(country):
        if kind == "india_registry":
            return 10
        if kind == "india_web":
            return 4
    return 0


def minimum_query_count(country: str | None) -> int:
    """Ensure country-specific sources are attempted before an early stop."""
    if is_india(country):
        # CHEMEXCIL + CDSCO + Pharmexcil + индийские сайты.
        return 4
    if is_china(country):
        # Английский, китайский и поиск по .cn.
        return 3
    if country:
        return 3
    return 2


# Сколько подтверждённых названий уходит в запрос как альтернативы. Без
# CAS-номера они заменяют его в роли якоря, но длинная цепочка OR
# размывает выдачу сильнее, чем добавляет охвата.
_MAX_NAME_ALTERNATIVES = 3


def _name_group(name: str, synonyms: list[str] | None) -> str:
    """Группа равнозначных названий: ("бетаин" OR "trimethylglycine").

    Заменяет CAS-номер в роли якоря, когда номера нет. Название хуже
    номера тем, что неуникально, поэтому чем больше подтверждённых
    человеком вариантов, тем точнее попадание.
    """
    names = [name]
    seen = {name.casefold()}
    for synonym in synonyms or []:
        cleaned = synonym.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            names.append(cleaned)
        if len(names) > _MAX_NAME_ALTERNATIVES:
            break
    if len(names) == 1:
        return f'"{names[0]}"'
    return "(" + " OR ".join(f'"{item}"' for item in names) + ")"


def build_search_queries(
    *,
    cas: str | None,
    name: str,
    country: str | None,
    ai_query: str | None,
    synonyms: list[str] | None = None,
) -> list[str]:
    """Build an Echemi-first plan followed by regional verification sources.

    CAS-номера может не быть: у смесей, рецептур и промышленных продуктов
    его нет и не будет. Тогда якорем становится группа подтверждённых
    названий. Точность при этом ниже — номер уникален, название нет, — и
    маркетплейсы в выдаче поднимаются, потому что оптимизируются как раз
    под товарные названия. Это компенсируется реестром посредников и
    отметками закупщика, но полностью не снимается.
    """
    country_term = f" {country}" if country else ""
    # Якорь запроса: с номером — название и номер вместе, без номера —
    # группа равнозначных названий.
    group = _name_group(name, synonyms)
    subject = f'"{name}" "{cas}"' if cas else group
    # Короткий якорь для запросов, где раньше стоял один номер.
    identifier = f'"{cas}"' if cas else group
    # Отдельных запросов к торговой площадке больше нет: в режиме поиска
    # изготовителей её карточки всё равно откладываются, а в режиме «все
    # продавцы» она находится обычным запросом. Два места в плане из восьми
    # уходят на поиск самих компаний.
    candidates: list[str | None] = []

    if is_china(country):
        candidates.extend(
            [
                f"{subject} (manufacturer OR factory) China",
                f"{identifier} (生产厂家 OR 工厂) 中国",
                f"{identifier} (manufacturer OR factory) site:.cn",
            ]
        )
    elif is_india(country):
        site_group = f'("{name}" OR "{cas}")' if cas else group
        candidates.extend(
            [
                f"site:chemexcil.in {site_group}",
                f"site:cdsco.gov.in {site_group} (GMP OR manufacturer OR API)",
                f"site:pharmexcil.com {site_group}",
                f"site:.in {identifier} (manufacturer OR factory OR producer)",
                f"{subject} (manufacturer OR producer OR factory) India",
            ]
        )
    elif country:
        candidates.append(f'{subject} manufacturer factory "{country}"')

    candidates.extend(
        [
            ai_query,
            f"{subject} manufacturer supplier{country_term} CoA",
        ]
    )

    unique: list[str] = []
    for query in candidates:
        normalized = (query or "").strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique
