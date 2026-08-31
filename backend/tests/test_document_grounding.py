import pytest

from app.schemas.document_verification import DocumentVerification
from app.services.document_verification import (
    _batch_claim_matches_quote, apply_document_verification, document_kind_from_text,
)


@pytest.mark.parametrize("quote", [
    "Batch No. CF-2026-01", "BatchNo CF-2026-01", "Batch No.:CF-2026-01",
    "Batch number CF-2026-01", "Lot No. CF-2026-01", "Batch: CF-2026-01",
    "Номер партии CF-2026-01", "Lot No. CF-2026-01 MFG date 2026-04-01",
])
def test_realistic_batch_labels_preserve_exact_identifier(quote):
    assert _batch_claim_matches_quote("CF-2026-01", quote)
    assert not _batch_claim_matches_quote("CF-2026-0", quote)
    assert not _batch_claim_matches_quote("CF-2026-011", quote)


@pytest.mark.parametrize("quote", ["Batch quality approved", "No batch provided", "BatchNo", "missing", "Batch No. OTHER-1"])
def test_non_batch_text_and_mismatches_fail_closed(quote):
    assert not _batch_claim_matches_quote("CF-2026-01", quote)


def verification(claims):
    return DocumentVerification.model_validate(dict(
        document_kind="coa", substance_match="exact", verification_status="confirmed",
        recommended_action="accept", confidence=95, reason="Evidence provided by the synthetic document.",
        claims=claims, missing_fields=[], red_flags=[],
    ))


def test_coa_without_colon_is_confirmed_but_wrong_cas_still_rejected():
    doc = "Certificate of Analysis\nCAS 58-08-2\nBatchNo CF-2026-01"
    claims = [
        dict(claim_type="chemical_identity", claim_value="58-08-2", quote="CAS 58-08-2"),
        dict(claim_type="batch", claim_value="CF-2026-01", quote="BatchNo CF-2026-01"),
    ]
    result = apply_document_verification(verification=verification(claims), document_text=doc, expected_cas="58-08-2")
    assert result["status"] == "confirmed"
    result = apply_document_verification(verification=verification(claims), document_text=doc, expected_cas="50-78-2")
    assert result["status"] == "rejected"


@pytest.mark.parametrize("label,accepted", [("Issue Date", False), ("Analysis Date", False), ("Test Date", False), ("MFG date", True), ("Manufacture date", True)])
def test_document_issue_date_cannot_be_used_as_manufacturing_date(label, accepted):
    quote = f"{label}: 2026-04-01"
    result = apply_document_verification(
        verification=verification([dict(claim_type="manufacture_date", claim_value="2026-04-01", quote=quote)]),
        document_text=quote, expected_cas="58-08-2",
    )
    assert bool(result["accepted_claims"]) is accepted
    assert bool(result["rejected_claims"]) is not accepted


def test_tds_reference_to_coa_does_not_change_its_kind_or_verify_a_batch():
    doc = "Technical Data Sheet\nCAS 58-08-2\nSee Certificate of Analysis for batch-specific data."
    assert document_kind_from_text(doc) == "tds"
    result = apply_document_verification(
        verification=verification([dict(claim_type="chemical_identity", claim_value="58-08-2", quote="CAS 58-08-2")]),
        document_text=doc, expected_cas="58-08-2",
    )
    assert result["status"] == "needs_review"
    assert "TDS" in result["gate_reason"]


@pytest.mark.parametrize("quote,accepted", [("ITEM STANDARD Result", False), ("Standard: BP2020 / EP10.0 / USP-NF2024", True)])
def test_pharmacopoeia_must_be_named_in_the_source_not_inferred(quote, accepted):
    result = apply_document_verification(
        verification=verification([dict(claim_type="standard", claim_value="BP/EP/USP", quote=quote)]),
        document_text=quote, expected_cas="58-08-2",
    )
    assert bool(result["accepted_claims"]) is accepted
