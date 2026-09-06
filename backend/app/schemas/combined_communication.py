"""Контракты явной групповой отправки нескольких RFQ одному поставщику."""

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import Channel


class CombinedRfqPositionRead(BaseModel):
    rfq_id: int
    name: str
    cas: str | None
    volume: str | None


class CombinedRfqOptionRead(BaseModel):
    supplier_id: int
    supplier_company: str
    channel: Channel
    positions: list[CombinedRfqPositionRead]


class CombinedRfqSelection(BaseModel):
    supplier_id: int = Field(gt=0)
    channel: Channel
    rfq_ids: list[int] = Field(min_length=2, max_length=50)

    @field_validator("rfq_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(value))
        if len(normalized) < 2 or any(rfq_id <= 0 for rfq_id in normalized):
            raise ValueError("Явно выберите не менее двух RFQ")
        return normalized


class CombinedRfqPreviewRead(BaseModel):
    supplier_id: int
    supplier_company: str
    channel: Channel
    rfq_ids: list[int]
    subject: str | None
    body: str


class CombinedRfqDispatchCreate(CombinedRfqSelection):
    idempotency_key: str = Field(min_length=36, max_length=36)
    confirm_external_send: bool = False

    @model_validator(mode="after")
    def validate_key(self) -> "CombinedRfqDispatchCreate":
        try:
            from uuid import UUID

            UUID(self.idempotency_key)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный ключ отправки") from exc
        return self


class CombinedRfqDispatchRead(CombinedRfqPreviewRead):
    communication_id: int
    status: str
