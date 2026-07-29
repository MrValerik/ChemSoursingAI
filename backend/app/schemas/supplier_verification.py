"""Structured output contract for the independent supplier auditor."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SubstanceMatch = Literal["exact", "probable", "analogue", "mismatch", "unknown"]
VerifiedSupplierRole = Literal["manufacturer", "distributor", "unverified"]
VerificationStatus = Literal["confirmed", "needs_review", "rejected"]
VerificationAction = Literal["shortlist", "manual_review", "reject"]


class SupplierVerification(BaseModel):
    result_index: int = Field(..., ge=0, le=59)
    substance_match: SubstanceMatch
    supplier_role: VerifiedSupplierRole
    verification_status: VerificationStatus
    recommended_action: VerificationAction
    confidence: int = Field(..., ge=0, le=100)
    reason: str = Field(..., min_length=5, max_length=1200)
    supporting_claim_ids: list[int] = Field(default_factory=list, max_length=10)
    contradictory_claim_ids: list[int] = Field(default_factory=list, max_length=10)
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)


SUPPLIER_VERIFICATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "result_index": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 59,
                    },
                    "substance_match": {
                        "type": "string",
                        "enum": [
                            "exact",
                            "probable",
                            "analogue",
                            "mismatch",
                            "unknown",
                        ],
                    },
                    "supplier_role": {
                        "type": "string",
                        "enum": [
                            "manufacturer",
                            "distributor",
                            "unverified",
                        ],
                    },
                    "verification_status": {
                        "type": "string",
                        "enum": ["confirmed", "needs_review", "rejected"],
                    },
                    "recommended_action": {
                        "type": "string",
                        "enum": ["shortlist", "manual_review", "reject"],
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "reason": {
                        "type": "string",
                        "minLength": 5,
                        "maxLength": 1200,
                    },
                    "supporting_claim_ids": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "contradictory_claim_ids": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "missing_evidence": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {"type": "string", "maxLength": 500},
                    },
                },
                "required": [
                    "result_index",
                    "substance_match",
                    "supplier_role",
                    "verification_status",
                    "recommended_action",
                    "confidence",
                    "reason",
                    "supporting_claim_ids",
                    "contradictory_claim_ids",
                    "missing_evidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}
