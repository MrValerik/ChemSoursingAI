"""Мощность как вторая опора короткого списка вместо упоминания документа.

Ворота требовали, чтобы к заявлению «мы производитель» прилагался
сертификат, CoA или TDS. Замер по 129 кандидатам завершённых прогонов:
хоть какое-то упоминание есть у 34%, а подтверждённых среди них ноль —
по нашему же правилу сайт продавца сертификат не подтверждает.

У заказчика документы приходят перепиской за 2–4 дня после контакта, и
«получение полного комплекта документов» названо узким местом. Требовать
их на этапе поиска — требовать того, чего на странице не бывает: завод
со скупым сайтом вылетал, а маркетинговая строка «CoA provided with
every batch» проходила.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_production_facts.db")

from app.services.page_facts import find_production_facts
from app.services.supplier_scoring import score_supplier


def _claim(claim_type: str, support: str = "supports") -> dict:
    return {
        "claim_type": claim_type,
        "support_status": support,
        "quote_verified": True,
    }


# --- чтение страницы ---


def test_chinese_capacity_is_read():
    text = "公司简介\n年产 20000 吨己二酸生产线\n联系我们"
    facts = find_production_facts(text)
    assert facts["production_capacity"] == "年产 20000 吨己二酸生产线"


def test_capacity_in_ten_thousand_tonnes_is_read():
    """«万吨» — обычная для китайских страниц единица."""
    text = "我们的产能达到 5 万吨"
    assert "production_capacity" in find_production_facts(text)


def test_english_annual_capacity_is_read():
    text = "About us\nOur annual capacity is 30,000 tons of phenyl trimethicone\n"
    facts = find_production_facts(text)
    assert "annual capacity" in facts["production_capacity"]


def test_tons_per_year_is_read():
    text = "The plant produces 12,000 metric tons per year"
    assert "production_capacity" in find_production_facts(text)


def test_own_plant_is_read():
    text = "We have our own factory in Shandong province"
    assert "production_site" in find_production_facts(text)


def test_a_trading_page_yields_nothing():
    """У перекупщика этих деталей нет — в этом и смысл признака."""
    text = (
        "We are a leading supplier of chemicals. Fast delivery, best price. "
        "Contact us for a quotation."
    )
    assert find_production_facts(text) == {}


def test_a_price_is_not_a_capacity():
    """«20 000 руб» — не тонны в год."""
    assert find_production_facts("Цена 20000 руб за партию") == {}


# --- вклад в короткий список ---


def test_capacity_opens_the_shortlist_without_any_document():
    """Завод со скупым сайтом больше не отсекается.

    Shandong Kerui в прогоне 61 набрала 85, доказала вещество и роль и
    была заблокирована только потому, что на странице нет ни CoA, ни TDS.
    """
    assessment = {"supplier_type": "manufacturer", "cas_status": "confirmed"}
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
        _claim("production_capacity"),
    ]

    score = score_supplier(assessment, evidence)

    assert score.shortlist_eligible is True
    assert score.total >= 70


def test_an_own_site_proves_the_role_by_itself():
    """Мы прочли «наш завод» со страницы и сами же звали роль неизвестной.

    У Shandong Kerui проверен production_site, а статус стоял «не
    определён»: понижение требовало именно manufacturer_role. Факт,
    прочитанный регуляркой, крепче прозаического «мы производитель».
    """
    from app.api.supplier_search import (
        SupplierQualification,
        _apply_evidence_gates,
    )

    qualification = SupplierQualification(
        result_index=0,
        company_name="Shandong Kerui Chemicals",
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
    payload = _apply_evidence_gates(
        qualification,
        [_claim("chemical_identity"), _claim("production_site")],
    )

    assert payload["supplier_type"] == "manufacturer"


def test_documents_alone_still_do_not_prove_the_role():
    """У дистрибьютора CoA есть тоже — бумага роли не доказывает."""
    from app.api.supplier_search import (
        SupplierQualification,
        _apply_evidence_gates,
    )

    qualification = SupplierQualification(
        result_index=0,
        company_name="Некто",
        title_ru="Оценка",
        summary_ru="Описание",
        supplier_type="manufacturer",
        cas_status="confirmed",
        country_status="claimed",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="claimed",
        tds_status="not_found",
        confidence=0,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )
    payload = _apply_evidence_gates(
        qualification, [_claim("chemical_identity"), _claim("coa")]
    )

    assert payload["supplier_type"] == "unknown"


def test_a_bare_manufacturer_claim_is_still_not_enough():
    """Снята одна опора, а не все: «мы завод» само по себе не проходит."""
    assessment = {"supplier_type": "manufacturer", "cas_status": "confirmed"}
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("country"),
    ]

    assert score_supplier(assessment, evidence).shortlist_eligible is False


def test_a_distributor_with_a_plant_still_does_not_pass():
    assessment = {"supplier_type": "distributor", "cas_status": "confirmed"}
    evidence = [
        _claim("chemical_identity"),
        _claim("manufacturer_role"),
        _claim("production_capacity"),
    ]

    assert score_supplier(assessment, evidence).shortlist_eligible is False
