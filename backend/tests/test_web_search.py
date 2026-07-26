"""Детерминированная проверка разбора поисковой выдачи без доступа в интернет."""

from app.connectors.web_search import parse_search_results


def test_parse_search_results_extracts_direct_source():
    page = """
    <div class="result">
      <a class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fproduct">
        Example &amp; Chemical
      </a>
      <a class="result__snippet">Official <b>manufacturer</b> product page</a>
    </div>
    """
    results = parse_search_results(page)
    assert results == [
        {
            "title": "Example & Chemical",
            "url": "https://example.com/product",
            "snippet": "Official manufacturer product page",
        }
    ]


def test_parse_search_results_accepts_reordered_attributes_without_snippet():
    page = """
    <div class="result">
      <a href="https://example.com/chemical"
         rel="nofollow"
         class="result__a">Chemical producer</a>
    </div>
    """
    assert parse_search_results(page) == [
        {
            "title": "Chemical producer",
            "url": "https://example.com/chemical",
            "snippet": "",
        }
    ]


def test_parse_search_results_rejects_non_http_links():
    page = """
    <div class="result">
      <a class="result__a" href="javascript:alert(1)">Unsafe result</a>
    </div>
    """
    assert parse_search_results(page) == []
