"""Шестая загруженная страница должна получать оценку наравне с первой.

Номер кандидата сквозной по всему списку, а пакет оценки — две страницы,
так что во втором пакете идут номера 2 и 3, в четвёртом — 6 и 7. Предел
на номер остался равным четырём с тех пор, когда пакетом был весь список
из пяти страниц.

Замер по сохранённым прогонам 214–252: у номеров 0–4 потерь нет ни одной,
а все 17 страниц с номером от пяти потеряли оценку целиком — страницу
загрузили, заплатили за неё и выбросили. Модель при этом возвращала
вместо запрещённого номера допустимый: номер 1 пришёл 38 раз при 23
реальных кандидатах. От приписывания страницы чужой компании спасала
только сверка номера с источником.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_candidate_index.db")

import pytest
from pydantic import ValidationError

from app.api.supplier_search import (
    MAX_QUALIFICATION_CANDIDATES,
    _QUALIFICATION_SCHEMA,
    SupplierQualification,
)


def _qualification(result_index: int) -> SupplierQualification:
    return SupplierQualification(
        result_index=result_index,
        company_name="Некто",
        title_ru="Оценка",
        summary_ru="Описание",
        supplier_type="unknown",
        cas_status="not_found",
        country_status="not_found",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="not_found",
        tds_status="not_found",
        confidence=0,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )


def test_the_sixth_candidate_has_an_expressible_number():
    assert _qualification(5).result_index == 5


def test_the_last_candidate_of_a_full_list_fits():
    last = MAX_QUALIFICATION_CANDIDATES - 1
    assert _qualification(last).result_index == last


def test_a_number_beyond_the_list_is_still_refused():
    with pytest.raises(ValidationError):
        _qualification(MAX_QUALIFICATION_CANDIDATES)


def test_the_model_is_told_the_same_limit():
    """Схема и проверка должны совпадать: расхождение и было причиной.

    Модели запрещали верный номер, а разбор потом отвергал подставленный.
    """
    schema_max = _QUALIFICATION_SCHEMA["properties"]["results"]["items"][
        "properties"
    ]["result_index"]["maximum"]
    assert schema_max == MAX_QUALIFICATION_CANDIDATES - 1
