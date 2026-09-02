"""Доказанная чужая страна стоит баллов, а не молчания.

Замер по 219 карточкам прогонов 280–320: у 45 страна доказанно чужая, и
балл у них почти как у верных — медиана 50 против 55, p90 73 против 86.
Причина в том, что несовпадение лишь не приносило десяти баллов, но ничего
не отнимало.

    Otto Chemie Pvt Ltd        Индия  81 балл в поиске по Китаю
    Pfizer Inc.                США    75
    Dr. Reddy's Laboratories   Индия  75
    Chemical Bull Pvt. Ltd.    Индия  75

Две такие карточки дошли до короткого списка — того самого, который
обещан закупщику как безопасный.

Штраф, а не исключение: у мирового производителя завод бывает и в нужной
стране при штаб-квартире в другой. Такую находку прячут не из списка, а
вниз списка.
"""

from app.services.supplier_scoring import (
    COUNTRY_MISMATCH_PENALTY,
    score_supplier,
)


def _evidence(*claim_types: str) -> list[dict]:
    return [
        {
            "claim_type": claim_type,
            "support_status": "supports",
            "quote_verified": True,
        }
        for claim_type in claim_types
    ]


_STRONG = ("chemical_identity", "manufacturer_role", "country", "iso")


def _assessment(**overrides) -> dict:
    base = {
        "supplier_type": "manufacturer",
        "country_status": "claimed",
        "cas_status": "confirmed",
    }
    base.update(overrides)
    return base


def test_the_requested_country_still_earns_its_points():
    score = score_supplier(
        _assessment(), _evidence(*_STRONG), identification_method="cas"
    )
    assert score.country == 10
    assert score.country_adjustment == 0
    assert score.shortlist_eligible


def test_a_proven_foreign_country_costs_points():
    """Раньше несовпадение лишь не приносило бонуса."""
    score = score_supplier(
        _assessment(country_status="mismatch"),
        _evidence(*_STRONG),
        identification_method="cas",
    )
    assert score.country == 0
    assert score.country_adjustment == COUNTRY_MISMATCH_PENALTY
    assert score.total == 54


def test_a_proven_foreign_company_never_reaches_the_short_list():
    """Короткий список — обещание безопасности, а не подборка похожего."""
    score = score_supplier(
        _assessment(country_status="mismatch"),
        _evidence(*_STRONG, "production_site", "gmp", "coa", "tds"),
        identification_method="cas",
    )
    assert not score.shortlist_eligible


def test_a_foreign_company_stays_below_one_whose_country_is_unknown():
    """«Доказанно не там» хуже, чем «неизвестно где»: медианы 50 и 36."""
    unknown = score_supplier(
        _assessment(country_status="not_found"),
        _evidence("chemical_identity", "manufacturer_role", "iso"),
        identification_method="cas",
    )
    foreign = score_supplier(
        _assessment(country_status="mismatch"),
        _evidence("chemical_identity", "manufacturer_role", "iso", "country"),
        identification_method="cas",
    )
    assert foreign.total < unknown.total


def test_a_wrong_substance_still_outweighs_everything():
    """Несовпадение вещества как обнуляло балл, так и обнуляет."""
    score = score_supplier(
        _assessment(country_status="mismatch", cas_status="mismatch"),
        _evidence(*_STRONG),
        identification_method="cas",
    )
    assert score.total == 0
    assert score.hard_exclusion
    assert score.country_adjustment == 0
