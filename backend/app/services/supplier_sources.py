"""Search plans and source classification for supplier sourcing."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class MarketProfile:
    """Что именно меняется от страны к стране в поисковом запросе.

    Замеры показали, что различий ровно три, и смешивать их в цепочке
    условий незачем:

    1. **Язык слов роли.** По числу результатов языки равны, но выдача
       разная: китайский запрос поднял Perstorp и Shin-Nakamura, которых
       английский не показал ни разу.
    2. **Ограничение по зоне.** Сужает всегда и иногда до нуля: на замере
       site:.cn дал 4 против 8, site:.ru — 6, а site:.in — ноль. Поэтому
       зона годится как один запрос из плана, но не как единственный.
    3. **Отраслевые реестры.** Есть только у части рынков.

    Общее для всех рынков — предмет поиска и намерение найти изготовителя.
    """

    role_terms: str
    # Название страны на языке запроса. Пользователь выбирает страну
    # по-русски, но «Китай» внутри английского запроса ищет хуже, чем
    # «China»: слово должно быть на языке остальной части строки.
    country_term: str | None = None
    site_scope: str | None = None
    registries: tuple[str, ...] = ()
    # Сколько запросов плана стоит выполнить до ранней остановки.
    min_queries: int = 3


_MARKET_CHINA = MarketProfile(
    role_terms="(manufacturer OR factory)",
    country_term="China",
    site_scope="site:.cn",
    min_queries=3,
)
_MARKET_INDIA = MarketProfile(
    role_terms="(manufacturer OR producer OR factory)",
    country_term="India",
    site_scope="site:.in",
    registries=(
        "site:chemexcil.in {subject}",
        "site:cdsco.gov.in {subject} (GMP OR manufacturer OR API)",
        "site:pharmexcil.com {subject}",
    ),
    # Три реестра, общий запрос и поиск по зоне.
    min_queries=5,
)
_MARKET_RUSSIA = MarketProfile(
    role_terms="(производитель OR изготовитель OR завод)",
    country_term="Россия",
    site_scope="site:.ru",
    min_queries=3,
)
_MARKET_DEFAULT = MarketProfile(
    role_terms="(manufacturer OR producer OR factory)",
    min_queries=2,
)


def is_russia(country: str | None) -> bool:
    return (country or "").strip().casefold() in {
        "россия",
        "russia",
        "рф",
        "ru",
    }


def market_profile(country: str | None) -> MarketProfile:
    """Правила построения запроса для рынка."""
    if is_india(country):
        return _MARKET_INDIA
    if is_china(country):
        return _MARKET_CHINA
    if is_russia(country):
        return _MARKET_RUSSIA
    profile = _MARKET_DEFAULT
    return profile if not country else MarketProfile(
        role_terms=profile.role_terms,
        min_queries=3,
    )


def minimum_query_count(country: str | None) -> int:
    """Ensure country-specific sources are attempted before an early stop."""
    return market_profile(country).min_queries


# Сколько подтверждённых названий уходит в запрос как альтернативы. Без
# CAS-номера они заменяют его в роли якоря, но длинная цепочка OR
# размывает выдачу сильнее, чем добавляет охвата.
_MAX_NAME_ALTERNATIVES = 3

# Точная фраза длиннее трёх слов не встречается в вебе целиком. Замер на
# «C18-C22 methacrylic acid pentaerythrityl ester»: в кавычках ноль
# результатов во всех странах, без кавычек — восемь. Порог измерен:
# названия в два слова находятся, в четыре — нет.
_MAX_QUOTED_WORDS = 3


def _is_quotable(name: str) -> bool:
    """Годится ли название в точную фразу."""
    return 0 < len(name.split()) <= _MAX_QUOTED_WORDS


def _distinct_names(name: str, synonyms: list[str] | None) -> list[str]:
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
    return names


def _name_group(name: str, synonyms: list[str] | None) -> str:
    """Группа равнозначных названий: ("бетаин" OR "trimethylglycine").

    Заменяет CAS-номер в роли якоря, когда номера нет. Название хуже
    номера тем, что неуникально, поэтому чем больше подтверждённых
    человеком вариантов, тем точнее попадание.

    В кавычки берутся только короткие названия. Длинное описательное имя
    в кавычках — гарантированный ноль: именно так поиск и молчал по
    веществам без номера, то есть ровно по тем, ради которых эта ветка
    и создавалась.
    """
    names = _distinct_names(name, synonyms)
    quotable = [item for item in names if _is_quotable(item)]
    if not quotable:
        # Ни одно название не годится в точную фразу — отдаём основное как
        # обычные слова. Ограничение задаст остальная часть запроса.
        return names[0]
    if len(quotable) == 1:
        return f'"{quotable[0]}"'
    return "(" + " OR ".join(f'"{item}"' for item in quotable) + ")"


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
    profile = market_profile(country)
    localised = profile.country_term or country
    country_term = f" {localised}" if localised else ""
    # Якорь запроса: с номером — название и номер вместе, без номера —
    # группа равнозначных названий.
    group = _name_group(name, synonyms)
    quoted_name = f'"{name}"' if _is_quotable(name) else name
    subject = f'{quoted_name} "{cas}"' if cas else group
    # Короткий якорь для запросов, где раньше стоял один номер.
    identifier = f'"{cas}"' if cas else group
    # Отдельных запросов к торговой площадке больше нет: в режиме поиска
    # изготовителей её карточки всё равно откладываются, а в режиме «все
    # продавцы» она находится обычным запросом. Два места в плане из восьми
    # уходят на поиск самих компаний.
    # Порядок задан ценностью, потому что бюджет запросов кончается раньше
    # плана: отраслевые реестры подтверждают изготовителя, язык рынка
    # открывает компании, которых нет в англоязычной выдаче, а сужение по
    # зоне только режет — на замере site:.cn дал 4 результата против 8,
    # site:.ru — 6, site:.in — ноль.
    candidates: list[str | None] = [
        template.format(subject=subject) for template in profile.registries
    ]

    if is_china(country):
        # Китайский запрос поднял Perstorp и Shin-Nakamura, которых
        # английский не показал ни разу.
        candidates.append(f"{identifier} (生产厂家 OR 工厂) 中国")

    candidates.append(f"{subject} {profile.role_terms}{country_term}")

    # Длинное название в кавычки не попало — даём ему отдельный заход
    # обычными словами, иначе оно вообще не участвует в поиске.
    if not _is_quotable(name):
        candidates.append(f"{name} {profile.role_terms}{country_term}")

    if profile.site_scope:
        candidates.append(f"{identifier} {profile.role_terms} {profile.site_scope}")

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
