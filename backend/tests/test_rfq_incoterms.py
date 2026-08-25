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
    """Похожий базис подставлять нельзя: он определяет, кто платит."""
    with pytest.raises(UnsupportedIncotermError):
        normalize_incoterm("DDP")
    with pytest.raises(UnsupportedIncotermError):
        normalize_incoterm("")


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
        RFQCreate(**_create(incoterms=["DDP"]))
    assert "DDP" in str(exc.value)


def test_rejection_names_the_supported_set():
    with pytest.raises(ValidationError) as exc:
        RFQCreate(**_create(incoterms=["DDP"]))
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
        incoterms=["DDP", "cfr"],
    )
    subject, body = render_rfq_text(stored)

    assert "Betaine" in subject
    # Базис показан ровно тот, что сохранён, — и без выдуманного места:
    # место определяет, где переходят риск и расходы.
    assert "  - DDP — DDP" in body
    assert "  - CFR — CFR" in body


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


def test_new_request_still_refuses_unknown_basis():
    """Снисходительность — только для чтения; отправить такое нельзя."""
    with pytest.raises(UnsupportedIncotermError):
        build_rfq(
            RFQInput(name="Betaine", incoterms=["DDP"]),
        )
