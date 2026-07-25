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
