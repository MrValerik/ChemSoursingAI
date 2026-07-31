import os

# Ранжирование живёт в API-модуле, который при импорте создаёт engine.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_supplier_sources.db")

from app.services.supplier_sources import (
    build_search_queries,
    is_india,
    minimum_query_count,
    source_kind,
)


def test_echemi_queries_are_always_first():
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="China",
        ai_query='"Aspirin" manufacturer China',
    )

    assert queries[0].startswith('site:echemi.com "50-78-2"')
    assert queries[1].startswith('site:echemi.com "50-78-2"')


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
