"""Запрос без CAS-номера: способы идентификации предмета закупки.

Раньше запрос нельзя было сохранить без номера. Но номер есть не у всего,
что закупают: у смесей, рецептур, полимеров и промышленных продуктов его
нет и не будет. Такой запрос — не «неизвестная молекула», а спецификация,
и отправить по ней RFQ вполне можно.

Отдельная тема здесь — что именно означает неудачная проверка по PubChem.
Опечатка в номере, отсутствие вещества в базе и недоступность самой базы
раньше выглядели одинаково.
"""

import os

import pytest
from pydantic import ValidationError

# Модуль ранжирования при импорте создаёт engine.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_identification.db")

from app.connectors.pubchem import SubstanceInfo
from app.schemas.rfq import RFQCreate
from app.services.cas import suggest_check_digit
from app.services.rfq_builder import RFQInput, build_rfq
from app.services.supplier_sources import build_search_queries


def _create(**kw) -> dict:
    base = dict(name="Бетаин", incoterms=["CIP"], search_countries=["Китай"])
    base.update(kw)
    return base


# --- способ идентификации ---


def test_request_by_specification_needs_no_cas():
    """Смесь или рецептура не имеет номера, но закупается."""
    data = RFQCreate(
        **_create(
            identification_method="spec",
            name="Загуститель для шампуня",
            specification="Вязкость 4000-6000 сП, неионогенный",
        )
    )
    assert data.cas is None


def test_request_by_analog_needs_the_reference_substance():
    """«Аналог» без эталона — это не задание, а пожелание."""
    with pytest.raises(ValidationError):
        RFQCreate(**_create(identification_method="analog"))

    data = RFQCreate(
        **_create(
            identification_method="analog",
            analog_reference="107-43-7",
            analog_variations=["salt", "manufacturer"],
        )
    )
    assert data.analog_reference == "107-43-7"


def test_request_by_cas_still_requires_the_number():
    """Выбран поиск по номеру — номер обязателен."""
    with pytest.raises(ValidationError):
        RFQCreate(**_create(identification_method="cas"))


def test_specification_mode_requires_something_to_search_by():
    """Без назначения и требований поставщику нечего отправить."""
    with pytest.raises(ValidationError):
        RFQCreate(**_create(identification_method="spec"))


def test_wrong_check_digit_names_the_correct_number():
    """Контрольная цифра вычисляется, поэтому её не гадают, а называют.

    Закупщик не должен сверять номер вручную, если верный вариант можно
    посчитать здесь же.
    """
    assert suggest_check_digit("107-43-8") == "107-43-7"
    # Верный номер подсказывать не о чем.
    assert suggest_check_digit("107-43-7") is None

    with pytest.raises(ValidationError) as exc:
        RFQCreate(**_create(cas="107-43-8"))
    assert "107-43-7" in str(exc.value)


# --- письмо поставщику ---


def test_letter_without_cas_does_not_print_none():
    """«CAS None» в письме выглядит как ошибка системы."""
    result = build_rfq(
        RFQInput(
            name="Загуститель для шампуня",
            incoterms=["CIP"],
            identification_method="spec",
            specification="Вязкость 4000-6000 сП",
        )
    )
    assert "None" not in result["subject"]
    assert "None" not in result["body"]
    assert "CAS" not in result["subject"]
    assert "Вязкость 4000-6000 сП" in result["body"]


def test_analog_letter_states_the_limits_of_substitution():
    """Без границ «analog» означает для поставщика что угодно."""
    result = build_rfq(
        RFQInput(
            name="Бетаин",
            incoterms=["CIP"],
            identification_method="analog",
            analog_reference="Glycine betaine",
            analog_variations=["salt"],
        )
    )
    body = result["body"]
    assert "Glycine betaine" in body
    assert "salt" in body


# --- план поисковых запросов ---


def test_queries_without_cas_use_confirmed_names_as_the_anchor():
    """Номер уникален, название нет — якорем служат подтверждённые имена."""
    queries = build_search_queries(
        cas=None,
        name="Cocamidopropyl betaine",
        country="China",
        ai_query=None,
        synonyms=["CAPB", "Coco betaine"],
    )

    assert queries
    # Пустая кавычковая группа испортила бы выдачу.
    assert not any('""' in query for query in queries)
    assert all("None" not in query for query in queries)
    joined = " ".join(queries)
    assert "Cocamidopropyl betaine" in joined
    assert "CAPB" in joined


def test_queries_with_cas_are_unchanged():
    """Ветка с номером не должна пострадать от появления второй.

    Один запрос идёт намеренно без номера: номер сужает выдачу до страниц,
    где он напечатан, а крупный производитель может его не печатать. Замер
    на эпоксидированном соевом масле — по одному названию находится Hairma,
    крупнейший в мире, с номером не находится никто.
    """
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="China",
        ai_query=None,
    )
    with_cas = [q for q in queries if '"50-78-2"' in q]
    assert len(with_cas) >= len(queries) - 1
    assert all("Aspirin" in query for query in queries)


def test_name_group_is_capped():
    """Длинная цепочка OR размывает выдачу сильнее, чем расширяет охват."""
    queries = build_search_queries(
        cas=None,
        name="Betaine",
        country=None,
        ai_query=None,
        synonyms=[f"synonym-{i}" for i in range(20)],
    )
    joined = " ".join(queries)
    assert joined.count("synonym-") <= len(queries) * 3


# --- почему проверка не удалась ---


def test_failed_verification_reports_which_of_three_things_happened():
    """Опечатка, отсутствие в базе и недоступность базы — разные факты.

    Раньше все три давали found=False и выглядели для закупщика как
    «вещество не подтверждено», хотя третий случай вообще не факт о
    веществе, а факт о нас.
    """
    assert SubstanceInfo(cas="1", found=True).outcome == "confirmed"
    assert (
        SubstanceInfo(cas="1", found=False, error="invalid_cas_checksum").outcome
        == "invalid_checksum"
    )
    assert (
        SubstanceInfo(cas="1", found=False, error="not_found").outcome == "not_found"
    )
    assert (
        SubstanceInfo(cas="1", found=False, error="http_error: timeout").outcome
        == "unavailable"
    )


def test_outcome_reaches_the_api_payload():
    """Различие бесполезно, если не доезжает до интерфейса."""
    payload = SubstanceInfo(cas="1", found=False, error="not_found").as_dict()
    assert payload["outcome"] == "not_found"
