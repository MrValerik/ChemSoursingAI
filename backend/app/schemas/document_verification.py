"""Контракт независимой проверки паспорта качества (CoA/TDS).

Агент интерпретирует недоверенный документ, поэтому каждое утверждение обязано
опираться на дословную цитату из сохранённого текста. Проверку цитат и
итоговое решение выполняет детерминированный код, а не модель.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocumentKind = Literal["coa", "tds", "msds", "other", "unknown"]
SubstanceMatch = Literal["exact", "probable", "analogue", "mismatch", "unknown"]
DocumentStatus = Literal["confirmed", "needs_review", "rejected"]
DocumentAction = Literal["accept", "manual_review", "reject"]
ClaimType = Literal[
    "chemical_identity",
    "batch",
    "manufacture_date",
    "expiry_date",
    "standard",
    "assay",
    "impurity",
    "manufacturer",
    "conclusion",
]


class DocumentClaim(BaseModel):
    claim_type: ClaimType
    claim_value: str = Field(..., min_length=1, max_length=300)
    quote: str = Field(..., min_length=3, max_length=600)


class DocumentVerification(BaseModel):
    document_kind: DocumentKind
    substance_match: SubstanceMatch
    verification_status: DocumentStatus
    recommended_action: DocumentAction
    confidence: int = Field(..., ge=0, le=100)
    reason: str = Field(..., min_length=5, max_length=1200)
    claims: list[DocumentClaim] = Field(default_factory=list, max_length=12)
    missing_fields: list[str] = Field(default_factory=list, max_length=8)
    red_flags: list[str] = Field(default_factory=list, max_length=8)


DOCUMENT_VERIFICATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "document_kind": {
            "type": "string",
            "enum": ["coa", "tds", "msds", "other", "unknown"],
        },
        "substance_match": {
            "type": "string",
            "enum": ["exact", "probable", "analogue", "mismatch", "unknown"],
        },
        "verification_status": {
            "type": "string",
            "enum": ["confirmed", "needs_review", "rejected"],
        },
        "recommended_action": {
            "type": "string",
            "enum": ["accept", "manual_review", "reject"],
        },
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string", "minLength": 5, "maxLength": 1200},
        "claims": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "claim_type": {
                        "type": "string",
                        "enum": [
                            "chemical_identity",
                            "batch",
                            "manufacture_date",
                            "expiry_date",
                            "standard",
                            "assay",
                            "impurity",
                            "manufacturer",
                            "conclusion",
                        ],
                    },
                    "claim_value": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 300,
                    },
                    "quote": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 600,
                    },
                },
                "required": ["claim_type", "claim_value", "quote"],
                "additionalProperties": False,
            },
        },
        "missing_fields": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 200},
        },
        "red_flags": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 300},
        },
    },
    "required": [
        "document_kind",
        "substance_match",
        "verification_status",
        "recommended_action",
        "confidence",
        "reason",
        "claims",
        "missing_fields",
        "red_flags",
    ],
    "additionalProperties": False,
}
