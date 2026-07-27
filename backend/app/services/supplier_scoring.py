"""Deterministic, explainable scoring of supplier evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SupplierScore:
    total: int
    identity: int
    supplier_role: int
    country: int
    documents: int
    evidence_quality: int
    hard_exclusion: bool
    shortlist_eligible: bool

    def to_dict(self) -> dict:
        return asdict(self)


def score_supplier(assessment: dict, evidence: list[dict]) -> SupplierScore:
    supported = {
        item["claim_type"]
        for item in evidence
        if item.get("support_status") == "supports"
        and item.get("quote_verified") is True
    }
    contradicted = {
        item["claim_type"]
        for item in evidence
        if item.get("support_status") == "contradicts"
        and item.get("quote_verified") is True
    }
    hard_exclusion = (
        "chemical_identity" in contradicted
        or assessment.get("cas_status") == "mismatch"
    )
    if hard_exclusion:
        return SupplierScore(0, 0, 0, 0, 0, 0, True, False)

    identity = 35 if "chemical_identity" in supported else 0
    supplier_type = assessment.get("supplier_type")
    supplier_role = (
        25
        if supplier_type == "manufacturer" and "manufacturer_role" in supported
        else 5
        if supplier_type == "distributor" and "manufacturer_role" in supported
        else 0
    )
    country = 10 if "country" in supported else 0
    documents = sum(
        weight
        for claim_type, weight in (("gmp", 5), ("iso", 4), ("coa", 3), ("tds", 3))
        if claim_type in supported
    )
    evidence_quality = 0
    if evidence and all(item.get("quote_verified") is True for item in evidence):
        evidence_quality += 10
    if len(supported) >= 3:
        evidence_quality += 5

    total = min(
        100,
        identity + supplier_role + country + documents + evidence_quality,
    )
    shortlist_eligible = (
        total >= 70
        and supplier_type == "manufacturer"
        and "chemical_identity" in supported
        and "manufacturer_role" in supported
    )
    return SupplierScore(
        total=total,
        identity=identity,
        supplier_role=supplier_role,
        country=country,
        documents=documents,
        evidence_quality=evidence_quality,
        hard_exclusion=False,
        shortlist_eligible=shortlist_eligible,
    )
