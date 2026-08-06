"""Перечень продавцов на площадке — не компания без роли, а площадка.

Замер по эталону: 18 из 21 ошибки классификации приходились на статус
«не определён», и добрая половина из них были страницы площадок. Роль им
приписывать нечего — у страницы нет роли, — но и молчать о том, что это
площадка, значит выдавать незнание за неопределённость.

Магазин одной компании внутри площадки под правило не подпадает: он
называет предприятие. Крупнейший в мире производитель эпоксидированного
соевого масла собственного сайта не имеет вовсе и существует только
магазином на площадке — назвав его площадкой, мы бы его потеряли.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_marketplace_kind.db")

from app.api.supplier_search import (
    SupplierQualification,
    _apply_evidence_gates,
)

DOMAINS = {"made-in-china.com", "app17.com", "kitairu.net", "chemball.cn"}


def _qualification(**kw) -> SupplierQualification:
    base = dict(
        result_index=0,
        company_name="Некая компания",
        title_ru="Оценка",
        summary_ru="Описание",
        supplier_type="manufacturer",
        cas_status="confirmed",
        country_status="claimed",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="not_found",
        tds_status="not_found",
        confidence=0,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )
    base.update(kw)
    return SupplierQualification(**base)


def _gate(url: str, **kw) -> dict:
    return _apply_evidence_gates(
        _qualification(**kw),
        [],
        page_url=url,
        intermediary_domains=DOMAINS,
    )


def test_a_listing_page_is_called_a_marketplace():
    payload = _gate("https://www.app17.com/supply/offerdetail/1417556.html")
    assert payload["supplier_type"] == "marketplace"


def test_a_category_page_of_a_marketplace_is_a_marketplace():
    payload = _gate(
        "https://kitairu.net/measurement-and-analysis-instruments/931251.html"
    )
    assert payload["supplier_type"] == "marketplace"


def test_a_company_storefront_is_judged_on_its_content():
    """Магазин называет предприятие — роль решается доказательствами."""
    payload = _gate(
        "https://megawidechem.en.made-in-china.com/product/Behenyl-Amine.html"
    )
    assert payload["supplier_type"] != "marketplace"


def test_a_company_site_is_untouched():
    payload = _gate("https://www.keruichemical.com/behenyl-dimethylamine/")
    assert payload["supplier_type"] != "marketplace"


def test_without_a_registry_nothing_is_reclassified():
    payload = _apply_evidence_gates(
        _qualification(), [], page_url="https://www.app17.com/x", intermediary_domains=set()
    )
    assert payload["supplier_type"] != "marketplace"
