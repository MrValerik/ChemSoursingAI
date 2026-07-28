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
