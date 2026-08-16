"""Подтверждение страны засчитывается только за ту страну, где искали.

Прогон 275 по запросу #30 искал в Китае. Модель сама записала по Simson
Pharma доказательство `country = "India"` с цитатой «+91 8767360663»,
поставила статус «страна вероятна» — и подсчёт начислил 10 баллов из 10,
потому что смотрел на наличие claim, а не на его значение. Индийская
компания набрала 63 балла в поиске по Китаю и попала в список.

Промпт при этом составлен верно: «mismatch — при явном указании другой
страны». Модель ошиблась, а ворота её не поправили, потому что снимали
статус только у claim, помеченного как contradicts.

Правило умеет запрещать и не умеет подтверждать: страну, которую не
удалось узнать в строке, оно не трогает.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_country.db")

from app.api.supplier_search import _apply_evidence_gates, SupplierQualification
from app.services.search_countries import (
    contradicts_search_country,
    mentioned_countries,
)
from app.services.supplier_scoring import score_supplier


def _claim(value: str, *, claim_type: str = "country") -> dict:
    return {
        "claim_type": claim_type,
        "claim_value": value,
        "support_status": "supports",
        "quote_verified": True,
    }


def _qualification(**overrides) -> SupplierQualification:
    fields = {
        "result_index": 0,
        "company_name": "Simson Pharma Limited",
        "title_ru": "Оценка",
        "summary_ru": "Описание",
        "page_kind": "company_site",
        "supplier_type": "unknown",
        "cas_status": "confirmed",
        "country_status": "likely",
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "confidence": 63,
        "red_flags": [],
        "missing_evidence": [],
        "evidence": [],
    }
    fields.update(overrides)
    return SupplierQualification(**fields)


# --- узнавание страны в свободной строке ---


def test_the_same_country_is_recognised_in_every_spelling():
    """Модель пишет страну как придётся: замер дал 43 написания на 873 claim."""
    for value in ("China", "Китай", "中国", "Zibo City, Shandong Province, China"):
        assert mentioned_countries(value) == frozenset({"Китай"})


def test_a_foreign_country_contradicts_the_search():
    assert contradicts_search_country("India", "Китай")
    assert contradicts_search_country("Spain (implied by S.L.U.)", "Китай")
    assert contradicts_search_country("Turkey", "Китай")


def test_the_searched_country_named_anywhere_is_enough():
    """Компания с двумя адресами остаётся своей: важно, что искомая названа."""
    assert not contradicts_search_country("США/Китай", "Китай")
    assert not contradicts_search_country(
        "Сингапур (компания), Китай (происхождение)", "Китай"
    )


def test_a_multi_country_search_accepts_either_market():
    assert not contradicts_search_country("India", "Китай и Индия")


def test_an_unrecognised_string_is_left_alone():
    """«likely» и «Ningbo» страну не называют — запрещать тут нечего."""
    for value in ("likely", "claimed", "Ningbo", "imported", "IN", "Europe"):
        assert not contradicts_search_country(value, "Китай")


def test_a_two_letter_code_never_fires_inside_a_sentence():
    """«located in Hebei» не должно читаться как Индия по коду IN."""
    assert mentioned_countries("China (implied by domain), located in Hebei") == (
        frozenset({"Китай"})
    )


# --- ворота и подсчёт ---


def test_the_gate_marks_a_foreign_country_as_a_mismatch():
    payload = _apply_evidence_gates(
        _qualification(),
        [_claim("India")],
        search_country="Китай",
    )

    assert payload["country_status"] == "mismatch"
    assert any("Подтверждена другая страна" in f for f in payload["red_flags"])
    assert "Индия" in " ".join(payload["red_flags"])


def test_a_mismatch_earns_no_points_for_the_country():
    evidence = [_claim("India"), _claim("CAS 50-78-2", claim_type="chemical_identity")]
    payload = _apply_evidence_gates(
        _qualification(), evidence, search_country="Китай"
    )

    score = score_supplier(payload, evidence)

    assert score.country == 0
    assert score.identity == 35


def test_the_searched_country_still_earns_its_points():
    evidence = [_claim("China"), _claim("CAS 50-78-2", claim_type="chemical_identity")]
    payload = _apply_evidence_gates(
        _qualification(), evidence, search_country="Китай"
    )

    score = score_supplier(payload, evidence)

    assert payload["country_status"] == "likely"
    assert score.country == 10


def test_without_a_search_country_the_rule_stays_silent():
    """Квалификация старого запуска без страны ничего не теряет."""
    payload = _apply_evidence_gates(
        _qualification(), [_claim("India")], search_country=""
    )

    assert payload["country_status"] == "likely"
