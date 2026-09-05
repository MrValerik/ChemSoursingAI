"""Базисы поставки на входе в запрос.

Набор базисов раньше жил в двух местах: списком строк в форме и словарём
в генераторе письма. Форма предлагала CIP, FCA и EXW, а закупщик на
встрече назвал FOB и DAP — их не было ни там, ни там.

Отдельная тема — где именно отклоняется неподдерживаемый базис. Раньше
единственной проверкой был генератор письма, и запрос успевал создаться.
"""

import os

import pytest
from pydantic import ValidationError

# Модуль ранжирования при импорте создаёт engine.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_incoterms.db")

from app.models import RFQ
from app.schemas.rfq import RFQCreate
from app.services.incoterms import (
    SUPPORTED_INCOTERMS,
    UnsupportedIncotermError,
    normalize_incoterm,
    normalize_incoterms,
)
from app.services.rfq_builder import RFQInput, build_rfq
from app.services.rfq_service import render_rfq_text


def _create(**kw) -> dict:
    base = dict(
        name="Бетаин",
        identification_method="spec",
        incoterms=["CIP"],
        search_countries=["Китай"],
    )
    base.update(kw)
    return base


# --- приведение к справочнику ---


def test_case_and_spaces_are_forgiven():
    """«cip» и « EXW » закупщик пишет так же часто, как канонический код."""
    assert normalize_incoterm(" cip ") == "CIP"
    assert normalize_incoterms(["fob", " dap "]) == ["FOB", "DAP"]


def test_unknown_basis_is_refused_not_guessed():
    """Похожий базис подставлять нельзя: он определяет, кто платит.

    По умолчанию приведение строгое — так работает разбор файла со
    списком позиций, где опечатку никто не смотрит глазами.
    """
    with pytest.raises(UnsupportedIncotermError):
        normalize_incoterm("DDU")
    with pytest.raises(UnsupportedIncotermError):
        normalize_incoterm("")


# --- свой базис закупщика ---


def test_custom_basis_is_kept_as_typed():
    """Своё условие закупщик вписывает руками и отвечает за смысл."""
    assert normalize_incoterm("ddu", allow_custom=True) == "DDU"
    assert normalize_incoterm(" самовывоз ", allow_custom=True) == "САМОВЫВОЗ"
    assert normalize_incoterms(["CIP", "DDU"], allow_custom=True) == ["CIP", "DDU"]


def test_custom_basis_must_still_look_like_a_basis():
    """Разрешено своё условие, а не произвольный текст в письмо.

    Базис уходит в письмо поставщику отдельной строкой. Абзац, перенос
    строки и строка из одних знаков базисом не являются.
    """
    for bad in ("!!!", "-", "", "У" * 25, "Везём как договоримся, детали письмом"):
        with pytest.raises(UnsupportedIncotermError):
            normalize_incoterm(bad, allow_custom=True)


def test_custom_basis_never_carries_a_line_break():
    """Перенос строки разорвал бы перечень базисов в письме."""
    code = normalize_incoterm("самовывоз\nсо склада", allow_custom=True)
    assert code == "САМОВЫВОЗ СО СКЛАДА"


def test_file_import_does_not_get_custom_bases():
    """Разбор файла остаётся строгим.

    В форме закупщик видит, что вводит. В файле на 50 строк опечатку
    «CPI» никто не заметит, и она стала бы базисом молча.
    """
    from app.services.rfq_import import parse_import_row

    row = parse_import_row(2, {"name": "Бетаин", "incoterms": "CPI"})
    assert "incoterms" not in row.values
    assert row.warnings and "CPI" in row.warnings[0].message


def test_empty_list_is_refused():
    with pytest.raises(UnsupportedIncotermError):
        normalize_incoterms([])


def test_duplicates_collapse_keeping_first_position():
    assert normalize_incoterms(["CIP", "FOB", "cip"]) == ["CIP", "FOB"]


# --- проверка на входе в запрос ---


@pytest.mark.parametrize("code", SUPPORTED_INCOTERMS)
def test_every_supported_basis_is_accepted(code):
    data = RFQCreate(**_create(incoterms=[code]))
    assert data.incoterms == [code]


def test_fob_and_dap_are_accepted_together():
    """То, ради чего карточка и заведена."""
    data = RFQCreate(**_create(incoterms=["FOB", "DAP"]))
    assert data.incoterms == ["FOB", "DAP"]


def test_unsupported_basis_rejected_at_the_door():
    """Отказ приходит при разборе запроса, а не при сборке письма."""
    with pytest.raises(ValidationError) as exc:
        RFQCreate(**_create(incoterms=["!!!"]))
    assert "!!!" in str(exc.value)


def test_custom_basis_survives_the_form():
    """Условие вне редакции 2020 — не ошибка: закупщик так работает."""
    data = RFQCreate(**_create(incoterms=["CIP", "DDU"]))
    assert data.incoterms == ["CIP", "DDU"]


def test_rejection_names_the_supported_set():
    with pytest.raises(ValidationError) as exc:
        RFQCreate(**_create(incoterms=["!!!"]))
    message = str(exc.value)
    for code in SUPPORTED_INCOTERMS:
        assert code in message


def test_empty_selection_rejected():
    with pytest.raises(ValidationError):
        RFQCreate(**_create(incoterms=[]))


# --- сохранённые запросы ---


def test_stored_value_outside_the_reference_still_renders():
    """Старый запрос с базисом вне набора обязан открываться.

    Проверка идёт через тот же путь, которым карточку рисует API:
    `_to_read` на каждом чтении заново собирает текст письма из
    сохранённой записи. Строгая проверка внутри этой сборки роняла
    карточку целиком — закупщик не мог открыть собственный отправленный
    запрос из-за того, что справочник с тех пор изменили.
    """
    stored = RFQ(
        name="Betaine",
        identification_method="spec",
        specification="test",
        incoterms=["DDU", "самовывоз"],
    )
    subject, body = render_rfq_text(stored)

    assert "Betaine" in subject
    # Базис показан ровно тот, что сохранён, — и без выдуманного места:
    # место определяет, где переходят риск и расходы.
    assert "  - DDU — named place to be confirmed with the buyer" in body
    assert "  - САМОВЫВОЗ — named place to be confirmed with the buyer" in body


def test_stored_value_inside_the_reference_keeps_its_place():
    """Нестрогий режим не должен терять места поставки у знакомых базисов."""
    stored = RFQ(
        name="Betaine",
        identification_method="spec",
        specification="test",
        incoterms=["FOB"],
    )
    _, body = render_rfq_text(stored)
    assert "  - FOB — FOB Shanghai port, China" in body


def test_new_request_still_refuses_an_unreadable_basis():
    """Снисходительность — только для чтения; отправить такое нельзя."""
    with pytest.raises(UnsupportedIncotermError):
        build_rfq(
            RFQInput(name="Betaine", incoterms=["!!!"]),
        )
