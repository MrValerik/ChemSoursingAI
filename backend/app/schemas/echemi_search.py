from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EchemiSearchCreate(BaseModel):
    query: str = Field(min_length=1, max_length=200)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class EchemiSearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rfq_id: int | None = None
    query: str
    status: str
    message: str | None
    created_at: datetime
    finished_at: datetime | None
    results: list[dict]
    diagnostics: dict


class EchemiSearchSummary(BaseModel):
    id: int
    rfq_id: int | None = None
    query: str
    status: str
    message: str | None
    created_at: datetime
    finished_at: datetime | None
    result_count: int


class EchemiManualStatus(BaseModel):
    waiting: bool
    remaining_seconds: int = Field(ge=0, le=600)
