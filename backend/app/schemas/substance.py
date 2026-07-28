"""API-контракты глобального справочника химических веществ."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.cas import normalize_cas


def _clean_names(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = value.strip()
        if not name:
            continue
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(name)
    return cleaned


class SubstanceCreate(BaseModel):
    cas: str = Field(..., min_length=3, max_length=20)
    preferred_name: str = Field(..., min_length=2, max_length=255)
    synonyms: list[str] = Field(default_factory=list, max_length=50)
    excluded_names: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("cas")
    @classmethod
    def clean_cas(cls, value: str) -> str:
        return normalize_cas(value)

    @field_validator("preferred_name")
    @classmethod
    def clean_preferred_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("synonyms", "excluded_names")
    @classmethod
    def clean_name_lists(cls, values: list[str]) -> list[str]:
        return _clean_names(values)


class SubstanceUpdate(BaseModel):
    preferred_name: str | None = Field(default=None, min_length=2, max_length=255)
    synonyms: list[str] | None = Field(default=None, max_length=50)
    excluded_names: list[str] | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("preferred_name")
    @classmethod
    def clean_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("synonyms", "excluded_names")
    @classmethod
    def clean_optional_lists(cls, values: list[str] | None) -> list[str] | None:
        return _clean_names(values) if values is not None else None


class SubstanceDecision(BaseModel):
    action: Literal["confirm", "reject"]
    suggested_name: str = Field(..., min_length=2, max_length=255)
    preferred_name: str | None = Field(default=None, min_length=2, max_length=255)
    synonyms: list[str] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=4000)
    verification: dict | None = None

    @field_validator("suggested_name")
    @classmethod
    def clean_suggested_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("preferred_name")
    @classmethod
    def clean_decision_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("synonyms")
    @classmethod
    def clean_decision_synonyms(cls, values: list[str]) -> list[str]:
        return _clean_names(values)


class SubstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cas: str
    preferred_name: str
    synonyms: list[str]
    excluded_names: list[str]
    notes: str | None
    review_status: str
    verification: dict | None
    reviewed_by_id: int | None
    reviewed_by_name: str | None = None
    request_count: int = 0
    created_at: datetime
    updated_at: datetime
