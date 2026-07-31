"""Схемы документов поставщика."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SupplierDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int | None
    communication_id: int | None
    supplier_id: int | None
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    kind: str
    text_status: str
    page_count: int | None
    extraction_error: str | None
    extracted_at: datetime | None
    verification: dict[str, Any] | None
    created_at: datetime


class SupplierDocumentDetail(SupplierDocumentRead):
    # Текст возвращается отдельно: список документов не должен тянуть его весь.
    text_content: str | None


class DocumentVerificationRequest(BaseModel):
    # Пустое значение означает «взять вещество из карточки запроса».
    cas: str | None = None
    name: str | None = None
