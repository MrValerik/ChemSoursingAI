"""Прежнее имя владельца марки как равноправный якорь поиска.

Проверено на позиции заказчика «Dowsil 556 Cosmetic Grade Fluid»: агент
идентификации по шести прогонам (57, 58, 101, 102, 103, 112) возвращал
единственное имя — само торговое название, — и все запросы прогона были
перестановкой одних и тех же пяти слов. При этом собственная страница
завода Silibase называет продукт «Replacement product for Dow Corning®556
Cosmetic Grade Fluid», а Kemi-Works ставит прежнее имя прямо в адрес
страницы. Под нынешним именем марки эти заводы не находятся.
"""

from app.services.brand_aliases import brand_aliases
from app.services.supplier_sources import build_search_queries


def test_a_renamed_brand_keeps_the_product_descriptor():
    """Dow меняла слово марки, а описательную часть названия оставляла."""
    assert brand_aliases("Dowsil 556 Cosmetic Grade Fluid") == [
        "Dow Corning 556 Cosmetic Grade Fluid"
    ]


def test_the_rename_works_both_ways():
    """Заказчик приносит и старое имя, и новое."""
    assert brand_aliases("Dow Corning 556") == ["DOWSIL 556"]
    assert brand_aliases("Degussa Aerosil 200") == ["Evonik Aerosil 200"]
    assert brand_aliases("Evonik Tegosoft") == ["Degussa Tegosoft"]


def test_a_longer_brand_wins_over_a_shorter_one():
    """«dow» внутри «dow corning» дало бы «Rohm and Haas Corning»."""
    assert brand_aliases("Dow Corning 556") == ["DOWSIL 556"]


def test_a_brand_inside_a_word_is_not_a_brand():
    assert brand_aliases("Dowanol PM") == []
    assert brand_aliases("Zinc Ricinoleate") == []
    assert brand_aliases("") == []


def test_the_former_name_reaches_the_search_plan():
    queries = build_search_queries(
        cas=None,
        name="Dowsil 556 Cosmetic Grade Fluid",
        country="Китай",
        ai_query=None,
        identification_method="analog",
        analog_reference="Dowsil 556 Cosmetic Grade Fluid",
    )
    joined = " ".join(queries)
    assert "Dow Corning 556 Cosmetic Grade Fluid" in joined
    # Нынешнее имя никуда не делось: это группа равнозначных названий.
    assert "Dowsil 556 Cosmetic Grade Fluid" in joined


def test_the_plan_speaks_the_words_the_factories_use():
    """«replacement» и «countertype» пишут сами заводы, «equivalent» — не всегда."""
    queries = build_search_queries(
        cas=None,
        name="Dowsil 556 Cosmetic Grade Fluid",
        country="Китай",
        ai_query=None,
        identification_method="analog",
        analog_reference="Dowsil 556 Cosmetic Grade Fluid",
    )
    joined = " ".join(queries)
    assert "replacement" in joined
    assert "countertype" in joined
    assert "analogue" in joined
    assert "对标" in joined


def test_alternative_names_are_not_lost_when_nothing_fits_in_quotes():
    """Длинное имя в кавычки не помещается — но список имён не теряется.

    Раньше здесь оставалось только первое название, и синонимы пропадали
    молча именно у тех позиций, ради которых их и собирают.
    """
    queries = build_search_queries(
        cas=None,
        name="C18-C22 methacrylic acid pentaerythrityl ester",
        country="Китай",
        ai_query=None,
        synonyms=["pentaerythrityl tetrastearate"],
    )
    joined = " ".join(queries)
    assert "pentaerythrityl tetrastearate" in joined
