"""Второй заход поиска: от имени компании к её собственному сайту.

Замер по эталону: из четырёх известных производителей адипиновой кислоты
система нашла одного. При этом в выдаче упоминались двое — просто обзор,
где стоит имя завода, сам поставщиком не является, и кандидатом не
становился. Имя из обзора и есть недостающее звено.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_company_follow_up.db")

from app.api.supplier_search import _company_site_plan_items


def _result(title: str = "", snippet: str = "", url: str = "https://x.cn/a") -> dict:
    return {"title": title, "snippet": snippet, "url": url}


def test_a_name_from_a_review_becomes_a_query_for_its_own_site():
    results = [
        _result(
            title="Обзор рынка адипиновой кислоты",
            snippet="华鲁恒升产能 32 万吨, Shandong Quark Chemical Co., Ltd тоже в списке",
            url="https://review.example.com/adipic",
        )
    ]

    items = _company_site_plan_items(results, country="Китай")

    queries = [item.query for item in items]
    assert '"华鲁恒升" 官网' in queries
    assert '"Shandong Quark Chemical Co., Ltd" official site' in queries


def test_the_language_follows_the_name():
    results = [_result(snippet="华鲁恒升产能 32 万吨")]
    item = _company_site_plan_items(results, country="Китай")[0]
    assert item.language == "zh"
    assert item.source_type == "official_site"
    assert item.purpose == "manufacturer"


def test_a_company_already_in_the_results_is_not_searched_again():
    """Её сайт уже в выдаче — второй запрос ничего не добавит."""
    results = [
        _result(
            title="Shandong Quark Chemical Co., Ltd",
            url="https://shandongquark.com/adipic-acid",
        )
    ]

    assert _company_site_plan_items(results, country="Китай") == []


def test_the_number_of_follow_ups_is_bounded():
    """Каждый запрос стоит денег и слота бюджета."""
    snippet = " ".join(
        f"Company Number{n} Chemical Co., Ltd" for n in range(10)
    )
    items = _company_site_plan_items([_result(snippet=snippet)], country="Китай")
    assert len(items) <= 3


def test_a_brand_from_a_capacity_review_outranks_a_trading_house():
    """Лимит второго захода мал, и тратить его надо на заводы.

    На прогоне 66 он ушёл на Shandong Quark, Jinan Finer и
    «聊城润恒化工贸易» — торговый дом. 华鲁恒升 стоял в том же тексте и
    остался неспрошенным.
    """
    results = [
        _result(
            snippet=(
                "华鲁恒升产能 32 万吨. Shandong Quark Chemical Co., Ltd, "
                "Jinan Finer Chemical Co., Ltd, 聊城润恒化工贸易有限公司"
            )
        )
    ]

    queries = [item.query for item in _company_site_plan_items(results, country="Китай")]

    assert queries[0] == '"华鲁恒升" 官网'
    assert all("贸易" not in query for query in queries)


def test_the_substance_itself_is_not_mistaken_for_a_plant():
    """«环氧大豆油产能» — мощность по веществу, а не имя завода.

    На прогоне 67 два слота второго захода из трёх ушли на «环氧大豆油»
    и «湖北环氧大豆油».
    """
    results = [_result(snippet="环氧大豆油产能 30 万吨，山东凯瑞产能 5 万吨")]

    queries = [
        item.query
        for item in _company_site_plan_items(
            results,
            country="Китай",
            subject_names=["Epoxidized soybean oil", "环氧大豆油"],
        )
    ]

    assert all("环氧大豆油" not in query for query in queries)


def test_a_regulation_title_is_not_a_company():
    """«染物综合排放标准» — норматив, занявший слот на прогоне 68."""
    results = [_result(snippet="染物综合排放标准产能要求")]
    assert _company_site_plan_items(results, country="Китай") == []


def test_an_empty_result_set_asks_nothing():
    assert _company_site_plan_items([], country="Китай") == []
