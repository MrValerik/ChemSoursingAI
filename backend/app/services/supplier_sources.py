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
    "sigmaaldrich.cn",
    "merckmillipore.com",
    "merckgroup.com",
    "thermofisher.com",
    "thermofisher.cn",
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
    "chemscene.com",
    "pharmaffiliates.com",
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
    "chem960.com",
    "chemball.cn",
    "bio-equip.cn",
    "b2bdata.baidu.com",
    "globalsources.com",
    "cphi-online.com",
    "pharmaexcipients.com",
    "tracxn.com",
    "barentz-na.com",
    "specialchem.com",
    "ulprospector.com",
    "lookpolymers.com",
    "univarsolutions.com",
    "cmstudioplus.com",
    "daltosur.com",
    "volza.com",
    "iajps.com",
    "chemnet.com",
    "lookchem.com",
    "molbase.com",
    "chemblink.com",
    "chemeo.com",
    # Нормативные и испытательные сайты, где CAS встречается внутри списков,
    # SDS и руководств, но это не карточка производителя сырья. На лёгком
    # benchmark заказчика такие PDF вытесняли сайты компаний из первых пяти.
    "oecd.org",
    "roadmaptozero.com",
    "intertek.com",
    "intertek.com.cn",
    "hohenstein.cn",
    "sist.org.cn",
    "gdpepe.edu.cn",
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
    # У конкретного завода может не быть собственного сайта: его единственная
    # первичная страница живёт как магазин на площадке. Такой URL нельзя
    # потерять только из-за домена, иначе правило split_by_intermediary о
    # storefront никогда не доживёт до ранжирования.
    from app.services.intermediaries import (
        DEFAULT_INTERMEDIARIES,
        is_intermediary,
        marketplace_page_kind,
    )

    intermediary_domains = {domain for domain, _, _ in DEFAULT_INTERMEDIARIES}
    if (
        is_intermediary(url, intermediary_domains)
        and marketplace_page_kind(url) == "storefront"
    ):
        return False
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
    # Слова, которыми завод описывает собственное производство. Отличаются
    # от role_terms принципиально: «manufacturer» и «factory» — это
    # маркетинг, их пишет и перекупщик. «Annual output» перекупщик не
    # пишет, потому что выпуска у него нет.
    #
    # Замер на адипиновой кислоте, где рынок держат Shenma, Hualu Hengsheng
    # и Ляоянский НПЗ: покупательский запрос не нашёл ни одного из них,
    # производственный — троих. На карбомере тот же приём по-китайски
    # добавил Lubrizol и Tinci, отсутствовавших в англоязычной выдаче.
    output_terms: str = '"annual output" OR "annual capacity"'
    # Запрос на языке рынка о производстве. Пусто там, где своего языка у
    # рынка нет.
    native_output_query: str = ""
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
    # 产能 — производственная мощность, 万吨 — десять тысяч тонн,
    # 生产企业 — производящее предприятие. Замер: этот запрос вывел 华鲁 и
    # 神马 по адипиновой кислоте и Lubrizol с 天赐 по карбомеру, тогда как
    # англоязычные не показали ни одного из них.
    native_output_query="{subject} 产能 万吨 生产企业",
    site_scope="site:.cn",
    # Реестров нет, зато есть два производственных семейства.
    min_queries=4,
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
    output_terms='"тонн в год" OR "производственная мощность"',
    site_scope="site:.ru",
    min_queries=4,
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


def _query_base_name(name: str) -> str:
    """Главное товарное имя без пояснения закупщика в скобках.

    Скобки в реальном файле содержат либо полезное уточнение, либо служебное
    «grade not specified». Оба варианта нельзя делать единственным якорем:
    сайты называют товар ``Poloxamer 407``, а не повторяют комментарий из
    карточки закупки.
    """
    base, separator, _ = (name or "").partition("(")
    cleaned = base.strip() if separator else (name or "").strip()
    return cleaned or (name or "").strip()


def analog_product_description(name: str, reference: str | None) -> str:
    """Функциональное имя продукта без торгового эталона в начале строки."""
    cleaned_name = _query_base_name(name)
    cleaned_reference = (reference or "").strip()
    if cleaned_reference and cleaned_name.casefold().startswith(
        cleaned_reference.casefold()
    ):
        description = cleaned_name[len(cleaned_reference) :].strip(" -–—")
        if description:
            return description
    if cleaned_name.casefold() == cleaned_reference.casefold():
        # Реальные выгрузки часто кладут всю торговую строку сразу в оба поля:
        # ``DOWSIL 5-7113 Silicone Quat Microemulsion``. Первый токен здесь —
        # марка, следующий код с цифрой — каталожный продукт; всё после него
        # остаётся полезным функциональным поисковым якорем. Короткие хвосты
        # вроде ``ABIL 45 ME`` не расшифровываем: это было бы домыслом.
        tokens = cleaned_name.split()
        for index, token in enumerate(tokens[1:4], start=1):
            if any(char.isdigit() for char in token):
                remainder = tokens[index + 1 :]
                if len(remainder) >= 2 or (
                    len(remainder) == 1 and len(remainder[0]) > 5
                ):
                    return " ".join(remainder)
                break
        return ""
    return cleaned_name


def specification_search_terms(specification: str | None) -> str:
    """Короткий композиционный якорь из начала пользовательской спецификации."""
    head = (specification or "").split(";", 1)[0].strip()
    if head.casefold().startswith("inci "):
        head = head[5:].strip(": ")
    return " ".join(head.replace("(and)", " ").split())


def _has_searchable_qualifier(name: str) -> bool:
    """Нужно ли сделать отдельный запрос с пояснением в скобках."""
    _, separator, qualifier = (name or "").partition("(")
    if not separator:
        return False
    lowered = qualifier.casefold()
    return not any(
        marker in lowered
        for marker in (
            "not specified",
            "not separated",
            "не указан",
            "не определен",
            "не определён",
            "не разделен",
            "не разделён",
            "не разделены",
        )
    )


def _explicit_form_variants(name: str) -> list[str]:
    """Безопасные отдельные ветки для явно перечисленных форм вещества.

    ``free base or sulfate form not separated`` означает неопределённость
    закупки, а не одну длинную товарную фразу. Основной запрос уже покрывает
    свободное основание; здесь добавляется только явно названная соль. Никакие
    не указанные катионы или формы функция не придумывает.
    """
    base, separator, qualifier = (name or "").partition("(")
    if not separator:
        return []
    lowered = qualifier.rstrip(") ").casefold()
    variants: list[str] = []
    for spelling in ("sulfate", "sulphate"):
        if f"or {spelling}" in lowered:
            variants.append(f"{base.strip()} {spelling}")
    return variants


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
    identification_method: str = "cas",
    analog_reference: str | None = None,
    specification: str | None = None,
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
    if identification_method == "analog" and (analog_reference or name).strip():
        reference_name = (analog_reference or name).strip()
        reference = _name_group(reference_name, None)
        product_terms = analog_product_description(name, reference_name)
        composition_terms = specification_search_terms(specification)
        functional_subject = (
            f'"{product_terms}"' if _is_quotable(product_terms) else product_terms
        )
        candidates: list[str | None] = []
        if is_china(country):
            candidates.append(
                f"{reference} (替代品 OR 同等品) (生产厂家 OR 工厂) 中国"
            )
        candidates.extend(
            [
                f"{reference} (equivalent OR alternative OR substitute) "
                f"{profile.role_terms}{country_term}",
                (
                    f"{functional_subject} {composition_terms} "
                    f"{profile.role_terms}{country_term}"
                    if functional_subject and composition_terms
                    else None
                ),
                (
                    f"{reference} {functional_subject} "
                    f"{profile.role_terms}{country_term}"
                    if functional_subject
                    else None
                ),
                (
                    f"{composition_terms} {profile.role_terms}{country_term}"
                    if composition_terms
                    else None
                ),
                (
                    f"{reference} (equivalent OR substitute) "
                    f"{profile.role_terms} {profile.site_scope}"
                    if profile.site_scope
                    else None
                ),
                ai_query,
                f"{reference} INCI composition TDS alternative{country_term}",
            ]
        )
        unique: list[str] = []
        for query in candidates:
            normalized = (query or "").strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique
    # Якорь запроса: с номером — название и номер вместе, без номера —
    # группа равнозначных названий.
    base_name = _query_base_name(name)
    group = _name_group(base_name, synonyms)
    quoted_name = f'"{base_name}"' if _is_quotable(base_name) else base_name
    subject = f'{quoted_name} "{cas}"' if cas else group
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
        candidates.append(f"{subject} (生产厂家 OR 工厂) 中国")

    # Вопрос «кто производит» вместо «кто продаёт». Слова роли пишет и
    # перекупщик, слова выпуска — только тот, у кого есть выпуск. На
    # адипиновой кислоте покупательский запрос не нашёл ни одного из трёх
    # лидеров рынка, а эти два семейства нашли всех.
    if profile.native_output_query:
        candidates.append(profile.native_output_query.format(subject=subject))

    candidates.append(f"{subject} {profile.role_terms}{country_term}")

    # Один заход без номера. Номер сужает выдачу до страниц, где он
    # напечатан, а крупный производитель может его не печатать: замер на
    # эпоксидированном соевом масле — запрос по одному названию находит
    # Hairma, крупнейшего в мире, а тот же запрос с номером не находит
    # никого. Место в плане это стоит одного запроса.
    if cas and quoted_name != subject:
        candidates.append(f"{quoted_name} {profile.role_terms}{country_term}")

    for variant in _explicit_form_variants(name):
        variant_subject = f'"{variant}"' if _is_quotable(variant) else variant
        candidates.append(
            f"{variant_subject} {profile.role_terms}{country_term}"
        )

    if identification_method == "spec":
        specification_terms = specification_search_terms(specification)
        if specification_terms:
            candidates.extend(
                [
                    f"{subject} {specification_terms} "
                    f"{profile.role_terms}{country_term}",
                    f"{specification_terms} {profile.role_terms}{country_term}",
                ]
            )

    # Длинное название в кавычки не попало — даём ему отдельный заход
    # обычными словами, иначе оно вообще не участвует в поиске.
    if _has_searchable_qualifier(name):
        candidates.append(f"{name} {profile.role_terms}{country_term}")
    elif not _is_quotable(base_name):
        candidates.append(f"{base_name} {profile.role_terms}{country_term}")

    # Английский запрос о выпуске ушёл из головы плана в хвост. Голова
    # обязательная, а план ограничен восемью запросами: два запроса о
    # мощности вытесняли с конца запросы планировщика. На карбомере это
    # стоило трёх находок — прогон 47 находил Newman и Lubrizol запросами
    # «COA», «specification», «MSDS», а прогон 115 их уже не делал.
    #
    # Тоннаж печатает многотоннажное производство. Для специальной химии
    # такой запрос почти пуст: у карбомера он дал 2 результата против 9 у
    # адипиновой кислоты. Родной язык рынка остаётся в голове — он полезен
    # для обоих, — а английский вариант выполняется, если бюджет позволит.
    candidates.append(f"{subject} {profile.output_terms}{country_term}")

    if profile.site_scope:
        candidates.append(f"{subject} {profile.role_terms} {profile.site_scope}")

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
