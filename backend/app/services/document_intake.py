"""Приём вложений сообщений: сохранение файла и извлечение текста.

Отказ по одному вложению не должен ломать обработку письма, поэтому причина
записывается в метаданные коммуникации, а не выбрасывается наружу.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.document_storage import (
    DocumentTooLargeError,
    UnsupportedDocumentError,
    store_document,
)
from app.services.document_text import apply_extraction

logger = logging.getLogger(__name__)


def store_incoming_attachments(
    db: Session,
    *,
    rfq_id: int | None,
    communication_id: int | None,
    supplier_id: int | None = None,
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Сохраняет вложения сообщения и возвращает метаданные без содержимого."""
    saved: list[dict[str, Any]] = []
    for attachment in attachments or []:
        filename = str(attachment.get("filename") or "document")
        declared = attachment.get("content_type")
        payload = attachment.get("content")
        metadata: dict[str, Any] = {
            "filename": filename,
            "content_type": declared,
            "size": attachment.get("size") or (len(payload) if payload else 0),
        }
        gateway_error = attachment.get("error")
        if gateway_error:
            metadata["status"] = "failed"
            metadata["error"] = str(gateway_error)[:300]
            saved.append(metadata)
            continue
        if not isinstance(payload, (bytes, bytearray)):
            metadata["status"] = "skipped"
            metadata["error"] = "Содержимое вложения недоступно"
            saved.append(metadata)
            continue
        try:
            stored = store_document(
                db,
                payload=bytes(payload),
                filename=filename,
                declared_content_type=declared,
                rfq_id=rfq_id,
                communication_id=communication_id,
                supplier_id=supplier_id,
            )
        except (DocumentTooLargeError, UnsupportedDocumentError) as exc:
            metadata["status"] = "rejected"
            metadata["error"] = str(exc)
            saved.append(metadata)
            continue
        except OSError as exc:
            logger.exception("Не удалось сохранить вложение %s", filename)
            metadata["status"] = "failed"
            metadata["error"] = str(exc)[:300]
            saved.append(metadata)
            continue

        document = stored.document
        if stored.created:
            apply_extraction(document)
            db.flush()
        metadata.update(
            {
                "document_id": document.id,
                "content_type": document.content_type,
                "size": document.size_bytes,
                "sha256": document.sha256,
                "kind": document.kind,
                "status": document.text_status,
                "page_count": document.page_count,
                "error": document.extraction_error,
            }
        )
        saved.append(metadata)
    return saved
