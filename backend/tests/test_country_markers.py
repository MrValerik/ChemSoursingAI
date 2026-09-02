"""Следы страны, читаемые со страницы без модели.

Замер по 48 карточкам прогонов 280–320, где страна осталась ненайденной:
доменная зона искомой страны — у двенадцати, телефон с кодом страны — у
пяти, лицензия ICP — у двух. Итого проверяемый признак есть у 18 из 48.

Правило намеренно однобокое: признак подтверждает искомую страну и никогда
её не опровергает. Чужой телефон на странице торговой компании — обычное
дело, и вывод «компания не там» по нему делать нельзя. Опровержение
остаётся за моделью, которая читает цитату в контексте.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_country_markers.db")

from app.api.supplier_search import (
    SupplierQualification,
    _apply_evidence_gates,
    _country_code,
)
from app.services.page_facts import find_country_markers


def _claim(claim_type: str, value: str = "") -> dict:
    return {
        "claim_type": claim_type,
        "claim_value": value,
        "support_status": "supports",
        "quote_verified": True,
    }


def _qualification(**overrides) -> SupplierQualification:
    fields = {
        "result_index": 0,
        "company_name": "Некто",
        "title_ru": "Оценка",
        "summary_ru": "Описание",
        "supplier_type": "manufacturer",
        "cas_status": "confirmed",
        "country_status": "claimed",
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "confidence": 0,
        "page_kind": "company_site",
        "red_flags": [],
        "missing_evidence": [],
        "evidence": [],
    }
    fields.update(overrides)
    return SupplierQualification(**fields)


# --- признаки на странице ---


def test_an_icp_licence_proves_a_site_in_mainland_china():
    """Номер ICP выдают только сайтам, размещённым в материковом Китае."""
    quote = find_country_markers("版权所有 京ICP备12345678号-1 北京化工", "cn")
    assert quote is not None
    assert "ICP备" in quote


def test_a_phone_with_the_country_code_counts():
    assert find_country_markers("Tel +86 138-0013-8000 Mr Wang", "cn")
    assert find_country_markers("Phone: +91 98765 43210 Mumbai", "in")


def test_a_bare_number_is_not_a_phone():
    """Иначе «86» из артикула стало бы доказательством страны."""
    assert find_country_markers("Product code 86 1234 grade A", "cn") is None


def test_a_foreign_code_proves_nothing_about_the_requested_country():
    """Чужой телефон на странице не делает компанию иностранной."""
    assert find_country_markers("Supplier in Germany +49 30 1234567", "cn") is None


def test_only_known_markets_are_read():
    assert _country_code("Китай") == "cn"
    assert _country_code("Индия") == "in"
    assert _country_code("Россия") == "ru"
    assert _country_code("Вьетнам") is None


# --- доменная зона в воротах ---


def test_the_domain_zone_makes_the_country_likely():
    """Двенадцать таких карточек сидели в «страна не найдена»."""
    payload = _apply_evidence_gates(
        _qualification(country_status="not_found"),
        [_claim("chemical_identity")],
        page_url="https://www.example-chem.cn/product",
        search_country="Китай",
    )
    assert payload["country_status"] == "likely"
    assert any("доменная зона" in flag for flag in payload["red_flags"])


def test_the_zone_of_another_country_changes_nothing():
    payload = _apply_evidence_gates(
        _qualification(country_status="not_found"),
        [_claim("chemical_identity")],
        page_url="https://www.example-chem.de/product",
        search_country="Китай",
    )
    assert payload["country_status"] == "not_found"


def test_a_proven_country_is_not_downgraded_by_the_zone():
    """Доказанная цитатой страна сильнее косвенного признака."""
    payload = _apply_evidence_gates(
        _qualification(country_status="claimed"),
        [_claim("chemical_identity"), _claim("country", "Китай")],
        page_url="https://www.example-chem.com/product",
        search_country="Китай",
    )
    assert payload["country_status"] == "claimed"


def test_the_zone_says_nothing_from_someone_elses_page():
    """Bayer помечался «вероятно Китай» со страницы китайского университета.

    Сайт размещён в стране — это факт о сайте, а не о названной на нём
    компании. Рядом так же помечались научный журнал и карточка площадки.
    """
    for kind in ("scientific", "market_report", "directory", "marketplace_listing"):
        payload = _apply_evidence_gates(
            _qualification(country_status="not_found", page_kind=kind),
            [_claim("chemical_identity")],
            page_url="https://bddg.hznu.edu.cn/article/123",
            search_country="Китай",
        )
        assert payload["country_status"] != "likely"


def test_the_zone_speaks_from_the_companys_own_site():
    payload = _apply_evidence_gates(
        _qualification(country_status="not_found", page_kind="company_site"),
        [_claim("chemical_identity")],
        page_url="https://www.ambeed.cn/products/1",
        search_country="Китай",
    )
    assert payload["country_status"] == "likely"


# --- номер лицензии как поле поставщика ---


def test_the_licence_number_is_read_from_the_page():
    """Единственный проверяемый признак китайской регистрации на страницах.

    Замер по 226 сохранённым страницам: лицензия нашлась на восемнадцати, а
    единый код кредитоспособности — ни на одной.
    """
    from app.services.page_facts import find_icp_licence

    assert find_icp_licence("版权所有 京ICP备12345678号-1 北京化工") == "京ICP备12345678号-1"
    assert find_icp_licence("© 2026 化工AI. 粤ICP备14075393号-") == "粤ICP备14075393号"
    # Пробелы внутри номера встречаются в вёрстке подвала.
    assert find_icp_licence("沪 ICP 备 09008123 号") == "沪ICP备09008123号"
    assert find_icp_licence("страница без лицензии") is None


def test_the_licence_reaches_the_card():
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity")],
        page_url="https://example-chem.cn/product",
        page_text="产品介绍 " + "x" * 400 + " 粤ICP备14075393号-1",
        fetch_status="completed",
        search_country="Китай",
    )
    assert payload["icp_licence"] == "粤ICP备14075393号-1"


def test_a_page_without_a_licence_leaves_the_field_empty():
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity")],
        page_url="https://example-chem.com/product",
        page_text="Product description " + "x" * 400,
        fetch_status="completed",
        search_country="Китай",
    )
    assert payload["icp_licence"] is None
