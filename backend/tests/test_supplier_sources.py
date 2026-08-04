import os

# Ранжирование живёт в API-модуле, который при импорте создаёт engine.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_supplier_sources.db")

from app.services.supplier_sources import (
    build_search_queries,
    is_india,
    minimum_query_count,
    source_kind,
    source_priority,
)


def test_plan_no_longer_reserves_places_for_a_marketplace():
    """Два запроса из плана раньше уходили на витрину ECHEMI.

    Карточка на площадке не подтверждает производство, а в режиме «все
    продавцы» площадка находится обычным запросом. Места в плане нужнее для
    поиска самих компаний.
    """
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="China",
        ai_query='"Aspirin" manufacturer China',
    )

    assert queries, "план не должен опустеть"
    assert not any(query.startswith("site:echemi.com") for query in queries)


def test_a_long_name_is_not_locked_into_an_exact_phrase():
    """Точная фраза длиннее трёх слов не встречается в вебе целиком.

    Замер на «C18-C22 methacrylic acid pentaerythrityl ester»: в кавычках
    ноль результатов во всех странах, без кавычек — восемь. Длинные
    описательные названия бывают как раз у веществ без CAS-номера, то есть
    ровно у тех, ради которых ветка поиска по названию и создавалась.
    """
    long_name = "C18-C22 methacrylic acid pentaerythrityl ester"
    queries = build_search_queries(
        cas=None, name=long_name, country="Китай", ai_query=None
    )

    assert queries
    assert all(f'"{long_name}"' not in query for query in queries)
    assert any(long_name in query for query in queries)


def test_a_short_name_stays_an_exact_phrase():
    """Короткое название в кавычках отсекает шум и находится."""
    queries = build_search_queries(
        cas=None, name="Cocamidopropyl betaine", country="Китай", ai_query=None
    )
    assert any('"Cocamidopropyl betaine"' in query for query in queries)


def test_a_long_name_still_gets_its_own_query_beside_short_synonyms():
    """Иначе длинное название вообще не участвует в поиске."""
    queries = build_search_queries(
        cas=None,
        name="C18-C22 methacrylic acid pentaerythrityl ester",
        country="Китай",
        ai_query=None,
        synonyms=["pentaerythrityl tetramethacrylate"],
    )
    assert any('"pentaerythrityl tetramethacrylate"' in q for q in queries)
    assert any(q.startswith("C18-C22 methacrylic acid") for q in queries)


def test_country_is_named_in_the_language_of_the_query():
    """Пользователь выбирает страну по-русски, но «Китай» внутри
    английского запроса ищет хуже, чем «China»."""
    queries = build_search_queries(
        cas="107-43-7", name="Betaine", country="Китай", ai_query=None
    )
    joined = " ".join(queries)
    assert "China" in joined
    assert "Китай" not in joined


def test_russia_asks_in_russian():
    """Раньше рынка России в плане не было вовсе."""
    queries = build_search_queries(
        cas="107-43-7", name="Betaine", country="Россия", ai_query=None
    )
    joined = " ".join(queries)
    assert "производитель" in joined
    assert "site:.ru" in joined


def test_marketplace_no_longer_outranks_a_company_site():
    """Площадка получала восемь баллов и обгоняла сайт завода."""
    assert source_priority("echemi", "Китай") == source_priority("web", "Китай")
    # Отраслевой реестр остаётся приоритетным: он подтверждает производителя.
    assert source_priority("india_registry", "Индия") > source_priority(
        "india_web", "Индия"
    )


def test_india_plan_uses_export_and_regulatory_sources():
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="Индия",
        ai_query=None,
    )

    assert is_india("Индия")
    required = queries[: minimum_query_count("India")]
    assert any("site:chemexcil.in" in query for query in required)
    assert any("site:cdsco.gov.in" in query for query in required)
    assert any("site:pharmexcil.com" in query for query in required)
    assert any("site:.in" in query for query in required)


def test_supplier_source_classification():
    assert (
        source_kind("https://www.echemi.com/shop-us123/index.html")
        == "echemi"
    )
    assert source_kind("https://chemexcil.in/members") == "india_registry"
    assert source_kind("https://examplechem.in/product") == "india_web"
    assert source_kind("https://example.com/product") == "web"


def test_distributors_and_reference_sites_are_not_treated_as_manufacturers():
    from app.services.supplier_sources import is_non_manufacturer_domain

    # Дистрибьюторы реактивов и продавцы стандартов: страница либо закрыта
    # защитой, либо всё равно отбраковывается как посредник.
    assert is_non_manufacturer_domain("https://www.sigmaaldrich.com/US/en/product/1")
    assert is_non_manufacturer_domain("https://store.usp.org/product/1044006")
    assert is_non_manufacturer_domain("https://www.thermofisher.com/order/x")
    # Справочники и маркетплейсы.
    assert is_non_manufacturer_domain("https://en.wikipedia.org/wiki/Aspirin")
    assert is_non_manufacturer_domain("https://www.chemicalbook.com/x.htm")
    assert is_non_manufacturer_domain("https://www.alibaba.com/product/x")
    assert is_non_manufacturer_domain("https://www.linkedin.com/company/x")
    # Настоящие сайты производителей и Echemi остаются доступными.
    assert not is_non_manufacturer_domain("https://www.fengchengroup.com/")
    assert not is_non_manufacturer_domain("https://www.echemi.com/produce/x.html")
    assert not is_non_manufacturer_domain("https://hebei-chem.cn/aspirin")


def test_ranking_drops_non_manufacturer_domains_before_fetching():
    from app.api.supplier_search import _rank_results

    results = [
        {"title": "Sigma", "url": "https://www.sigmaaldrich.com/p/1", "snippet": ""},
        {"title": "USP", "url": "https://store.usp.org/product/1044006", "snippet": ""},
        {"title": "Завод", "url": "https://www.fengchengroup.com/", "snippet": ""},
    ]
    ranked = _rank_results(results, "Китай", 10)
    urls = [item["url"] for item in ranked]
    assert urls == ["https://www.fengchengroup.com/"]
