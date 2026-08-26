"""API-контракты ролевых профилей и бюджета общения."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.communication_profiles import PROFILE_FIELDS


class CommunicationProfileBase(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_instructions: str = Field(min_length=20, max_length=12000)
    required_fields: list[str] = Field(default_factory=list, max_length=8)
    max_input_chars: int = Field(default=12000, ge=500, le=100000)
    max_auto_replies: int = Field(default=12, ge=1, le=100)
    max_duration_minutes: int = Field(default=10080, ge=10, le=525600)
    max_prompt_tokens: int = Field(default=60000, ge=1000, le=10_000_000)
    max_completion_tokens: int = Field(default=12000, ge=500, le=2_000_000)
    max_estimated_cost_usd: float = Field(default=10, ge=0.01, le=100000)

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value))
        unknown = set(normalized) - PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Неизвестные обязательные поля: {', '.join(sorted(unknown))}")
        if not normalized:
            raise ValueError("Профиль должен содержать хотя бы одно обязательное поле")
        return normalized


class CommunicationProfileCreate(CommunicationProfileBase):
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")


class CommunicationProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_instructions: str | None = Field(default=None, min_length=20, max_length=12000)
    required_fields: list[str] | None = Field(default=None, min_length=1, max_length=8)
    max_input_chars: int | None = Field(default=None, ge=500, le=100000)
    max_auto_replies: int | None = Field(default=None, ge=1, le=100)
    max_duration_minutes: int | None = Field(default=None, ge=10, le=525600)
    max_prompt_tokens: int | None = Field(default=None, ge=1000, le=10_000_000)
    max_completion_tokens: int | None = Field(default=None, ge=500, le=2_000_000)
    max_estimated_cost_usd: float | None = Field(default=None, ge=0.01, le=100000)
    is_active: bool | None = None

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value))
        unknown = set(normalized) - PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Неизвестные обязательные поля: {', '.join(sorted(unknown))}")
        return normalized


class CommunicationProfileRead(CommunicationProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    version: int
    is_active: bool
    is_system: bool
    updated_by: str | None
    updated_at: datetime


class CommunicationProfileVersionRead(CommunicationProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    version: int
    changed_by: str | None
    created_at: datetime


class CommunicationProfileAssignment(BaseModel):
    profile_id: int | None = None


class CommunicationProfileStatusRead(BaseModel):
    profile_id: int
    profile_slug: str
    profile_name: str
    profile_version: int
    source: str
    budget: dict
    stopped: bool
    stop_reason: str | None
    explanation: str
