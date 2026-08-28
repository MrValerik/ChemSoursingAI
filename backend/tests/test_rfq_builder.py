"""Тесты генератора RFQ."""

import pytest

from app.models.rfq import RFQ
from app.services.incoterms import INCOTERM_PLACES, SUPPORTED_INCOTERMS
from app.services.rfq_builder import (
    REQUIRED_DOCUMENTS,
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)
from app.services.rfq_service import external_rfq_name


def _sample(**kw):
    base = dict(cas="50-78-2", name="Acetylsalicylic acid", incoterms=["CIP", "FCA"])
    base.update(kw)
    return RFQInput(**base)


def test_subject_contains_name_and_cas():
    rfq = build_rfq(_sample())
    assert "Acetylsalicylic acid" in rfq["subject"]
    assert "50-78-2" in rfq["subject"]


def test_external_letter_uses_verified_latin_name_for_russian_card():
    rfq = RFQ(
        name="Ацетилсалициловая кислота",
        cas="50-78-2",
        verification={
            "synonyms": ["Aspirin", "Acetylsalicylic acid"],
            "iupac_name": "2-acetyloxybenzoic acid",
        },
    )

    assert external_rfq_name(rfq) == "Acetylsalicylic acid"


def test_body_lists_all_incoterms_and_docs():
    rfq = build_rfq(_sample(incoterms=["CIP", "FCA", "EXW"]))
    body = rfq["body"]
    for code in ("CIP", "FCA", "EXW"):
        assert code in body
    for doc in REQUIRED_DOCUMENTS:
        assert doc in body


def test_fields_structure():
    rfq = build_rfq(_sample())
    fields = rfq["fields"]
    assert fields["cas"] == "50-78-2"
    assert fields["incoterms"] == ["CIP", "FCA"]
    assert fields["required_documents"] == REQUIRED_DOCUMENTS


def test_incoterms_normalized():
    rfq = build_rfq(_sample(incoterms=["cip", " exw "]))
    assert rfq["fields"]["incoterms"] == ["CIP", "EXW"]


def test_empty_incoterms_rejected():
    with pytest.raises(UnsupportedIncotermError):
        build_rfq(_sample(incoterms=[]))


def test_unsupported_incoterm_rejected():
    with pytest.raises(UnsupportedIncotermError):
        build_rfq(_sample(incoterms=["DDP"]))


# --- набор базисов поставки ---


def test_supported_set_is_pinned():
    """Набор базисов зафиксирован и совпадает со списком в форме.

    Список во фронте (`NewRfq.tsx`, INCOTERM_OPTIONS) держится отдельно:
    у него есть русские пояснения, которых бэкенду не нужно. Разъехаться
    они не должны — этот тест ломается, если набор здесь изменили, а
    форму не тронули.
    """
    assert SUPPORTED_INCOTERMS == ("EXW", "FCA", "FOB", "CIP", "DAP")


@pytest.mark.parametrize("code", SUPPORTED_INCOTERMS)
def test_every_supported_incoterm_reaches_the_letter(code):
    """Каждый базис проходит сериализацию и попадает в текст письма."""
    rfq = build_rfq(_sample(incoterms=[code]))
    assert rfq["fields"]["incoterms"] == [code]
    assert rfq["fields"]["delivery_terms"] == [INCOTERM_PLACES[code]]
    # В письме базис назван вместе с местом поставки: Incoterm без
    # названного места не говорит, где переходят риск и расходы.
    assert f"  - {code} — {INCOTERM_PLACES[code]}" in rfq["body"]


def test_new_bases_are_available():
    """FOB и DAP — то, чего не хватало закупщику на встрече."""
    rfq = build_rfq(_sample(incoterms=["FOB", "DAP"]))
    assert rfq["fields"]["incoterms"] == ["FOB", "DAP"]


def test_incoterm_order_follows_the_buyer():
    """Порядок в письме — тот, в котором отметил закупщик, не канонический."""
    rfq = build_rfq(_sample(incoterms=["DAP", "EXW"]))
    assert rfq["fields"]["incoterms"] == ["DAP", "EXW"]


def test_duplicate_incoterms_collapse():
    """Повтор базиса не должен дублировать строку в письме."""
    rfq = build_rfq(_sample(incoterms=["CIP", "cip", " CIP "]))
    assert rfq["fields"]["incoterms"] == ["CIP"]
    assert rfq["body"].count("  - CIP — ") == 1


def test_rejection_names_the_supported_set():
    """Отказ называет доступные базисы, а не только факт отказа."""
    with pytest.raises(UnsupportedIncotermError) as exc:
        build_rfq(_sample(incoterms=["DDP"]))
    message = str(exc.value)
    for code in SUPPORTED_INCOTERMS:
        assert code in message
