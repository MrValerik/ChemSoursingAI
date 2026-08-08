"""Физический адрес против заявления о себе.

Доказывать производство по страницам поставщиков нечем: замер по 136
карточкам прогонов 214–264 дал ровно один номер государственной лицензии
и шесть упоминаний выпуска или площадки. Зато опровергать есть чем —
офисный адрес нашёлся у 62 карточек, и у 27 из 50 нынешних
«производителей» он стоит при полном отсутствии производственных фактов.

Внешние руководства по проверке поставщиков сходятся на двух вещах:
самоназвание роли не доказывает ни в какой формулировке, а завод стоит в
промзоне, тогда как посредник — в бизнес-центре. См.
docs/MANUFACTURER_EVIDENCE_RULES.md.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_office_address.db")

from app.services.page_facts import find_address_facts


def _claim(claim_type: str, support: str = "supports") -> dict:
    return {
        "claim_type": claim_type,
        "support_status": support,
        "quote_verified": True,
    }


def _qualification(**overrides):
    from app.api.supplier_search import SupplierQualification

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
        "red_flags": [],
        "missing_evidence": [],
        "evidence": [],
    }
    fields.update(overrides)
    return SupplierQualification(**fields)


# --- чтение адреса ---


def test_an_office_tower_address_is_read():
    """Строка Henan GP, с которой всё началось."""
    text = (
        "Contact us\n"
        "29th Floor, The Cloud Center, NO.138 Ruida Road, Zhengzhou 450000, China\n"
    )
    facts = find_address_facts(text)
    assert "29th Floor" in facts["office_address"]


def test_a_room_number_is_read():
    text = "Address: Room 1602, Ocean Plaza, No.1188 Siping Road, Shanghai"
    assert "office_address" in find_address_facts(text)


def test_a_plant_in_an_industrial_park_keeps_its_room_number():
    """У завода в промышленном парке тоже бывает номер корпуса.

    Признак промзоны где угодно на странице отменяет вывод целиком:
    понижать такую компанию не за что.
    """
    text = (
        "Room 5, Building 2, Juchao Economic Development Zone, Chaohu, Anhui\n"
        "Our plant runs three lines.\n"
    )
    assert find_address_facts(text) == {}


def test_a_page_without_an_address_yields_nothing():
    text = "We supply adipic acid of 99.7% purity. Contact us for a quotation."
    assert find_address_facts(text) == {}


# --- вывод о роли ---


def test_a_self_declared_factory_at_an_office_address_is_downgraded():
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(company_name="Henan GP Chemicals"),
        [
            _claim("chemical_identity"),
            _claim("manufacturer_role"),
            _claim("office_address"),
        ],
    )

    assert payload["supplier_type"] == "unknown"
    assert any("бизнес-центре" in flag for flag in payload["red_flags"])


def test_a_verifiable_detail_outweighs_the_office_address():
    """Завод с торговым офисом остаётся заводом.

    Годовой выпуск и собственная площадка читаются регуляркой со
    страницы, и против них контактный адрес не довод.
    """
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(),
        [
            _claim("chemical_identity"),
            _claim("production_capacity"),
            _claim("office_address"),
        ],
    )

    assert payload["supplier_type"] == "manufacturer"


def test_without_an_office_address_a_self_declaration_still_stands():
    """Правило снимает статус по признаку, а не подряд."""
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity"), _claim("manufacturer_role")],
    )

    assert payload["supplier_type"] == "manufacturer"


def test_an_office_address_with_a_trade_claim_gives_a_distributor():
    """Названо и заводом, и торговым домом — доказан торговый дом."""
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(),
        [
            _claim("chemical_identity"),
            _claim("manufacturer_role"),
            _claim("office_address"),
            _claim("reseller_role"),
        ],
    )

    assert payload["supplier_type"] == "distributor"
