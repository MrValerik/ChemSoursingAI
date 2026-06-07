"""Тесты генератора RFQ."""

import pytest

from app.services.rfq_builder import (
    REQUIRED_DOCUMENTS,
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)


def _sample(**kw):
    base = dict(cas="50-78-2", name="Acetylsalicylic acid", incoterms=["CIP", "FCA"])
    base.update(kw)
    return RFQInput(**base)


def test_subject_contains_name_and_cas():
    rfq = build_rfq(_sample())
    assert "Acetylsalicylic acid" in rfq["subject"]
    assert "50-78-2" in rfq["subject"]


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
