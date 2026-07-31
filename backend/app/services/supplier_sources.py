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
    """Prioritize Echemi globally and Indian registries for an India search."""
    if is_india(country):
        if kind == "india_registry":
            # Official/export-council evidence should remain visible even when
            # Echemi returns many marketplace cards for the same chemical.
            return 10
        if kind == "india_web":
            return 4
    if kind == "echemi":
        return 8
    return 0


def minimum_query_count(country: str | None) -> int:
    """Ensure country-specific sources are attempted before an early stop."""
    if is_india(country):
        # Echemi (2) + CHEMEXCIL + CDSCO + Pharmexcil + Indian websites.
        return 6
    if is_china(country):
        # Echemi (2) + English, Chinese-language and .cn searches.
        return 5
    if country:
        return 3
    return 2


def build_search_queries(
    *,
    cas: str,
    name: str,
    country: str | None,
    ai_query: str | None,
) -> list[str]:
    """Build an Echemi-first plan followed by regional verification sources."""
    country_term = f" {country}" if country else ""
    candidates: list[str | None] = [
        # ECHEMI officially supports product/CAS and supplier search. Search its
        # indexed product and shop pages first because it has no public API.
        f'site:echemi.com "{cas}" "{name}" (supplier OR manufacturer)',
        f'site:echemi.com "{cas}" ("Contact supplier" OR "Get Price" OR Inquiry)',
    ]

    if is_china(country):
        candidates.extend(
            [
                f'"{name}" "{cas}" (manufacturer OR factory) China',
                f'"{cas}" (生产厂家 OR 工厂) 中国',
                f'"{cas}" (manufacturer OR factory) site:.cn',
            ]
        )
    elif is_india(country):
        candidates.extend(
            [
                f'site:chemexcil.in ("{name}" OR "{cas}")',
                f'site:cdsco.gov.in ("{name}" OR "{cas}") (GMP OR manufacturer OR API)',
                f'site:pharmexcil.com ("{name}" OR "{cas}")',
                f'site:.in "{cas}" (manufacturer OR factory OR producer)',
                f'"{name}" "{cas}" (manufacturer OR producer OR factory) India',
            ]
        )
    elif country:
        candidates.append(
            f'"{name}" "{cas}" manufacturer factory "{country}"'
        )

    candidates.extend(
        [
            ai_query,
            f'"{name}" "{cas}" manufacturer supplier{country_term} CoA',
        ]
    )

    unique: list[str] = []
    for query in candidates:
        normalized = (query or "").strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique
