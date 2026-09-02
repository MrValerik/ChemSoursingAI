"""Поисковик, не найдя совпадений, подменяет вопрос — это не ответ.

Боевой прогон 322 по торговой марке «Plantacare 1200 UP» (без CAS): семь
запросов из девяти вернули ноль, а две непустые выдачи дали 20 находок, из
которых **ни одна** не упоминала марку — ролики с рецептами, словарная
статья на слово «official», заметка про майонез. Прогон упал на попытке
открыть словарь Merriam-Webster.

Причина выяснилась проверкой на самом Serper. Один и тот же запрос
«Plantacare 1200 UP composition ingredients»:

    без исключений  — 10 ссылок, 8 называют марку, среди них страница с
                      INCI Lauryl Glucoside и страница с CAS 110615-47-9
    с -site:        — 10 ссылок, 0 называют марку

Вычеркнутый ulprospector был первой ссылкой. Когда у запроса с
ограничениями не остаётся совпадений, поисковик снимает ограничения и
отдаёт что угодно. Отсюда два правила: исключения не применяются к
запросам на опознание вещества, а выдача без единого упоминания предмета
не принимается вовсе.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_relaxed_search.db")

from app.api.supplier_search import _mentions_subject, _subject_anchors
from app.services.supplier_sources import with_excluded_domains


# --- исключения не мешают опознанию ---


def test_a_supplier_query_still_strikes_out_dead_domains():
    query = with_excluded_domains("Betaine manufacturer China", "manufacturer")
    assert "-site:chemicalbook.com" in query


def test_an_identification_query_keeps_the_catalogues():
    """У торговой марки без номера каталог — единственный источник состава."""
    for purpose in ("product", "documents"):
        query = with_excluded_domains(
            "Plantacare 1200 UP composition ingredients", purpose
        )
        assert query == "Plantacare 1200 UP composition ingredients"


def test_a_query_aimed_at_a_domain_is_still_left_alone():
    query = 'site:echemi.com "107-43-7" betaine'
    assert with_excluded_domains(query, "manufacturer") == query


# --- подменённая выдача ---


def test_the_substance_name_anchors_the_answer():
    anchors = _subject_anchors(None, "Plantacare 1200 UP", None)
    assert "plantacare" in anchors
    # Короткое слово совпало бы с чем угодно.
    assert "up" not in anchors


def test_a_chinese_name_anchors_by_two_characters():
    anchors = _subject_anchors("59259-38-0", "乳酸薄荷酯", None)
    assert "乳酸薄荷酯" in anchors
    assert _mentions_subject(
        {"title": "乳酸薄荷酯 供应", "snippet": "", "url": "https://x.cn"}, anchors
    )


def test_a_page_about_recipes_is_not_about_the_substance():
    anchors = _subject_anchors(None, "Plantacare 1200 UP", None)
    assert not _mentions_subject(
        {
            "title": "3 ingredient recipes",
            "snippet": "1 teaspoon Instant Coffee",
            "url": "https://www.youtube.com/watch?v=x",
        },
        anchors,
    )


def test_the_catalogue_page_is_about_the_substance():
    anchors = _subject_anchors(None, "Plantacare 1200 UP", None)
    assert _mentions_subject(
        {
            "title": "PLANTACARE 1200 UP",
            "snippet": "INCI : Lauryl Glucoside",
            "url": "https://ami-ingredients.fr/x",
        },
        anchors,
    )


def test_the_number_alone_is_enough_of_an_anchor():
    anchors = _subject_anchors("110615-47-9", "Lauryl Glucoside", None)
    assert _mentions_subject(
        {"title": "", "snippet": "Ingredient Remark 110615-47-9", "url": "https://x"},
        anchors,
    )
