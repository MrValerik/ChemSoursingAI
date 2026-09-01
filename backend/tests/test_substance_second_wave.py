"""Второй заход по настоящему имени вещества.

У торговой марки без CAS искать нечем. Агент идентификации по шести
прогонам позиции «Dowsil 556 Cosmetic Grade Fluid» (57, 58, 101, 102, 103,
112) возвращал одно имя — само торговое название, — и весь план был
перестановкой одних и тех же пяти слов. Девять последних прогонов этой
позиции подряд дали ноль карточек.

При этом имя печатают сами страницы: страница завода Silibase обещает
заменить Dow Corning 556 и тут же ставит «INCI: Phenyl Trimethicone», а
CAS не даёт вовсе. Замер по сохранённой выдаче: слово INCI встречается в
302 сниппетах, и из 52 из них имя вынимается дословно.
"""

from app.api.supplier_search import _substance_name_plan_items
from app.services.page_facts import find_inci_names


def _result(title: str = "", snippet: str = "") -> dict:
    return {"title": title, "snippet": snippet, "url": "https://example.com/"}


# --- извлечение имени ---


def test_the_name_is_read_from_the_page_as_printed():
    assert find_inci_names("INCI: \u200b\nPhenyl Trimethicone\n\u200bApplications") == [
        "Phenyl Trimethicone"
    ]
    assert find_inci_names("INCI Name: Carbomer 940. CAS Number: 9003-01-4") == [
        "Carbomer 940"
    ]
    assert find_inci_names("INCI名称：EPOXIDIZED SOYBEAN OIL") == [
        "EPOXIDIZED SOYBEAN OIL"
    ]


def test_a_substring_inside_another_word_is_not_a_name():
    """«prINCIpals» встречается в сохранённых сниппетах и ловилось без границ."""
    assert find_inci_names("from our supplier / principals for your R&D") == []


def test_an_empty_field_and_a_section_heading_are_not_names():
    assert find_inci_names("INCI Name: Not available") == []
    assert find_inci_names("INCI: Detail - haut.de") == []
    assert find_inci_names("INCI: Number") == []


def test_a_ubiquitous_ingredient_is_no_anchor():
    """«aqua» стоит в составе почти всего и уведёт заход куда угодно."""
    assert find_inci_names("INCI: Aqua") == []


# --- запросы второго захода ---


def test_the_found_name_becomes_a_query_about_itself():
    plan = _substance_name_plan_items(
        [
            _result("Chinese factory of analogue Dow Corning 556",
                    "Replacement product. INCI: Phenyl Trimethicone"),
        ],
        country="Китай",
        known_names=["Dowsil 556 Cosmetic Grade Fluid"],
    )
    assert len(plan) == 1
    assert '"Phenyl Trimethicone"' in plan[0].query
    assert "manufacturer" in plan[0].query
    assert "China" in plan[0].query


def test_the_name_we_already_search_by_is_not_repeated():
    plan = _substance_name_plan_items(
        [_result(snippet="INCI: Phenyl Trimethicone")],
        country="Китай",
        known_names=["Phenyl Trimethicone"],
    )
    assert plan == []


def test_nothing_is_added_when_no_page_names_the_substance():
    plan = _substance_name_plan_items(
        [_result("Dowsil 556 supplier", "Buy Dowsil 556 online")],
        country="Китай",
        known_names=["Dowsil 556 Cosmetic Grade Fluid"],
    )
    assert plan == []


def test_the_second_wave_stays_short():
    """Заход стоит запросов, поэтому берётся не больше двух имён."""
    results = [
        _result(snippet=f"INCI: Ingredient {index}") for index in range(6)
    ]
    plan = _substance_name_plan_items(
        results, country="Китай", known_names=["Trade Name X"]
    )
    assert len(plan) == 2
