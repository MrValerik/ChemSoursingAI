"""Ворота объясняют роль, а не стирают её.

Замер по 204 карточкам прогонов 280–320: до ворот модель называет роль у
89% кандидатов — 133 производителя, 48 дистрибьюторов, 23 без ответа. До
закупщика «Не определено» доходило у 66%. Разбор движения:

    производитель -> не определено   95
    дистрибьютор  -> не определено   17
    не определено -> не определено   23

То есть 112 из 135 «не определено» — стёртый ответ, а не отсутствие
ответа. Причины по красным флагам: заявлено без доказательства — 70,
страница вообще не компании — 52, встречное свидетельство — 7, страница
не загрузилась — 4.

Решающее поле supplier_type здесь не меняется ни в одной ветви: на нём
держатся балл и короткий список — supplier_scoring требует именно
manufacturer. Новое поле role_proof объясняет, почему там стоит то, что
стоит.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_role_proof.db")

from app.api.supplier_search import SupplierQualification, _apply_evidence_gates


def _claim(claim_type: str, support: str = "supports") -> dict:
    return {
        "claim_type": claim_type,
        "support_status": support,
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


def test_the_answer_of_the_model_is_kept_as_it_was():
    """Ответ до ворот сохраняется целиком, каким бы он ни был."""
    payload = _apply_evidence_gates(
        _qualification(supplier_type="manufacturer"),
        [_claim("chemical_identity")],
    )
    assert payload["role_claimed"] == "manufacturer"
    # А решающее поле понижено, как и раньше.
    assert payload["supplier_type"] == "unknown"


def test_production_proof_makes_the_role_proven():
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity"), _claim("production_site")],
    )
    assert payload["supplier_type"] == "manufacturer"
    assert payload["role_proof"] == "proven"


def test_a_self_claim_is_a_weak_answer_not_a_missing_one():
    """Самая частая причина: 70 карточек из 135."""
    payload = _apply_evidence_gates(
        _qualification(), [_claim("chemical_identity")]
    )
    assert payload["supplier_type"] == "unknown"
    assert payload["role_proof"] == "claimed"
    assert payload["role_claimed"] == "manufacturer"


def test_an_office_address_contradicts_rather_than_silences():
    """Адрес в бизнес-центре — довод за торговую компанию, а не молчание."""
    payload = _apply_evidence_gates(
        _qualification(),
        [
            _claim("chemical_identity"),
            _claim("manufacturer_role"),
            _claim("office_address"),
        ],
    )
    assert payload["supplier_type"] == "unknown"
    assert payload["role_proof"] == "contradicted"


def test_a_page_that_is_not_a_company_says_so():
    """52 карточки из 135: обзоры, статьи, справочники, перечни."""
    payload = _apply_evidence_gates(
        _qualification(page_kind="market_report"),
        [_claim("chemical_identity"), _claim("production_site")],
    )
    assert payload["supplier_type"] == "unknown"
    assert payload["role_proof"] == "not_a_company_page"


def test_a_page_that_did_not_load_says_so():
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity")],
        page_url="https://example.com/",
        page_text="",
        fetch_status="completed",
    )
    assert payload["role_proof"] == "page_missing"


def test_a_wrong_substance_is_not_an_unknown_role():
    """MSN Chemical заявил CAS 61597-98-6 вместо требуемого 59259-38-0."""
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("chemical_identity", "contradicts")],
    )
    assert payload["cas_status"] == "mismatch"
    assert payload["role_proof"] == "substance_mismatch"


def test_a_trading_company_proved_by_its_own_words():
    payload = _apply_evidence_gates(
        _qualification(supplier_type="distributor"),
        [_claim("chemical_identity"), _claim("reseller_role")],
    )
    assert payload["supplier_type"] == "distributor"
    assert payload["role_proof"] == "proven"


def test_a_trading_company_named_only_by_the_model():
    payload = _apply_evidence_gates(
        _qualification(supplier_type="distributor"),
        [_claim("chemical_identity")],
    )
    assert payload["supplier_type"] == "distributor"
    assert payload["role_proof"] == "claimed"


def test_a_model_without_an_answer_stays_without_one():
    """Только эти 23 карточки и есть настоящая неопределённость."""
    payload = _apply_evidence_gates(
        _qualification(supplier_type="unknown"),
        [_claim("chemical_identity")],
    )
    assert payload["role_claimed"] == "unknown"
    assert payload["role_proof"] == "unknown"
