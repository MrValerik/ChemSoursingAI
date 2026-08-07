"""Под каким номером и какой маркой товар продаётся, а не описан.

Карбомер: в заявке 9003-01-4 — полиакриловая кислота, а косметический и
фармацевтический грейд торгуется под 9007-20-9 и марками 940, 980. Пока
каждый запрос нёс номер из заявки, ни один из семи известных поставщиков
не находился за три прогона.

Знание это агентское, не справочное, поэтому оно живёт отдельно от
SubstanceIdentity с её правилом «только факты PubChem» и проходит
детерминированную проверку: контрольная цифра CAS считается по самому
номеру, и выдуманный набор цифр её почти наверняка не пройдёт.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_market_aliases.db")

from app.api.supplier_search import (
    MarketAliases,
    SubstanceIdentity,
    SupplierSearchRequest,
    _fallback_search_plan,
    validated_market_aliases,
)


def _clean(**kw) -> MarketAliases:
    return validated_market_aliases(
        MarketAliases(**{"alternative_cas": [], "grade_names": [], **kw}),
        name=kw.pop("_name", "Carbomer"),
        cas=kw.pop("_cas", "9003-01-4"),
    )


# --- проверка добытого ---


def test_a_real_alternative_number_is_kept():
    result = validated_market_aliases(
        MarketAliases(alternative_cas=["9007-20-9"], grade_names=["Carbopol 940"]),
        name="Carbomer",
        cas="9003-01-4",
    )
    assert result.alternative_cas == ["9007-20-9"]
    assert result.grade_names == ["Carbopol 940"]


def test_an_invented_number_fails_the_check_digit():
    """Главная защита от фантазии: контрольная цифра считается по номеру."""
    result = validated_market_aliases(
        MarketAliases(alternative_cas=["1234-56-7", "9007-20-8"]),
        name="Carbomer",
        cas="9003-01-4",
    )
    assert result.alternative_cas == []


def test_the_requested_number_is_not_repeated():
    result = validated_market_aliases(
        MarketAliases(alternative_cas=["9003-01-4"]),
        name="Carbomer",
        cas="9003-01-4",
    )
    assert result.alternative_cas == []


def test_the_requested_name_is_not_repeated_as_a_grade():
    result = validated_market_aliases(
        MarketAliases(grade_names=["carbomer", "Carbopol 980"]),
        name="Carbomer",
        cas="9003-01-4",
    )
    assert result.grade_names == ["Carbopol 980"]


def test_empty_lists_are_a_normal_answer():
    result = validated_market_aliases(
        MarketAliases(), name="Adipic acid", cas="124-04-9"
    )
    assert result.alternative_cas == []
    assert result.grade_names == []


# --- как это попадает в поиск ---


def _plan(aliases: MarketAliases | None) -> list[str]:
    data = SupplierSearchRequest(name="Carbomer", cas="9003-01-4", country="Китай")
    identity = SubstanceIdentity(
        status="verified", canonical_name="Carbomer", search_names=["Carbomer"]
    )
    return [item.query for item in _fallback_search_plan(data, identity, aliases)]


def test_a_grade_is_searched_without_the_number():
    """В том и смысл: номер из заявки отсекал рынок целиком."""
    queries = _plan(MarketAliases(grade_names=["Carbopol 940"]))
    grade_queries = [q for q in queries if "Carbopol 940" in q]

    assert grade_queries
    assert all("9003-01-4" not in q for q in grade_queries)


def test_the_other_number_is_searched_with_the_name():
    queries = _plan(MarketAliases(alternative_cas=["9007-20-9"]))
    assert any("9007-20-9" in q and "Carbomer" in q for q in queries)


def test_the_grade_query_is_reached_before_the_plan_is_cut():
    """В хвосте марка не работает: план обрезается на восьми запросах.

    Первый прогон это и показал — этап вернул 9007-20-9 и Carbopol, а
    поиск шёл по-прежнему только по 9003-01-4.
    """
    queries = _plan(
        MarketAliases(alternative_cas=["9007-20-9"], grade_names=["Carbopol 940"])
    )
    grade_position = next(
        index for index, query in enumerate(queries) if "Carbopol 940" in query
    )
    number_position = next(
        index for index, query in enumerate(queries) if "9007-20-9" in query
    )

    assert grade_position < 4, queries[:6]
    assert number_position < 5, queries[:6]


def test_the_market_language_query_still_comes_first():
    """Голову плана марка занимает не целиком: проверенное идёт раньше."""
    queries = _plan(MarketAliases(grade_names=["Carbopol 940"]))
    assert "Carbomer" in queries[0]
    assert "Carbopol" not in queries[0]
