"""Безопасное хранение документов поставщика в контуре заказчика.

Файл считается недоверенными данными: имя, объявленный MIME-тип и содержимое
приходят снаружи. Поэтому путь в хранилище задаёт система по хешу содержимого,
тип определяется по сигнатуре файла, а размер ограничен настройкой.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SupplierDocument

# Типы, которые система умеет хранить и (кроме сканов) читать.
PDF = "application/pdf"
PLAIN_TEXT = "text/plain"
SUPPORTED_CONTENT_TYPES = {
    PDF,
    PLAIN_TEXT,
    "image/png",
    "image/jpeg",
    "image/tiff",
}

_KIND_PATTERNS = (
    ("coa", re.compile(r"\bco\s?a\b|certificate\s+of\s+analysis|сертификат\s+анализа|质量证书|检验报告", re.I)),
    ("tds", re.compile(r"\bt\s?d\s?s\b|technical\s+data\s+sheet|спецификац|техническ\w*\s+данн", re.I)),
    ("msds", re.compile(r"\bm?sds\b|safety\s+data\s+sheet|паспорт\s+безопасност", re.I)),
)


class DocumentTooLargeError(ValueError):
    """Файл превышает разрешённый размер."""


class UnsupportedDocumentError(ValueError):
    """Тип файла не поддерживается хранилищем."""


@dataclass(frozen=True)
class StoredDocument:
    document: SupplierDocument
    created: bool


def storage_root() -> Path:
    root = Path(get_settings().attachment_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sniff_content_type(payload: bytes, declared: str | None) -> str:
    """Определяет тип по сигнатуре: объявленному типу отправителя не доверяем."""
    if payload.startswith(b"%PDF-"):
        return PDF
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    normalized = (declared or "").split(";")[0].strip().lower()
    if normalized == PLAIN_TEXT:
        return PLAIN_TEXT
    return normalized or "application/octet-stream"


def guess_kind(filename: str) -> str:
    """Предварительная догадка по имени файла; решение принимает не она."""
    # Подчёркивания и дефисы разделяют слова в именах файлов, но для regex
    # являются символами слова: приводим их к пробелам до сопоставления.
    normalized = re.sub(r"[_\-.]+", " ", filename)
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(normalized):
            return kind
    return "other"


def safe_filename(filename: str) -> str:
    """Оставляет только имя файла без путей и управляющих символов."""
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "document"


def store_document(
    db: Session,
    *,
    payload: bytes,
    filename: str,
    declared_content_type: str | None = None,
    rfq_id: int | None = None,
    communication_id: int | None = None,
    supplier_id: int | None = None,
) -> StoredDocument:
    """Сохраняет файл на диск и создаёт запись.

    Повторная доставка того же письма не создаёт дубль: файл с тем же хешем в
    рамках одного запроса переиспользуется.
    """
    settings = get_settings()
    max_bytes = settings.attachment_max_size_mb * 1024 * 1024
    if len(payload) > max_bytes:
        raise DocumentTooLargeError(
            f"Файл больше {settings.attachment_max_size_mb} МБ и не сохранён"
        )
    if not payload:
        raise UnsupportedDocumentError("Пустой файл")

    content_type = sniff_content_type(payload, declared_content_type)
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedDocumentError(
            f"Тип файла {content_type} не поддерживается"
        )

    digest = hashlib.sha256(payload).hexdigest()
    existing = db.scalar(
        select(SupplierDocument).where(
            SupplierDocument.sha256 == digest,
            SupplierDocument.rfq_id == rfq_id,
        )
    )
    if existing is not None:
        return StoredDocument(document=existing, created=False)

    # Путь строится только из хеша: имя отправителя не участвует в пути.
    relative = Path(digest[:2]) / f"{digest}.bin"
    target = storage_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)

    clean_name = safe_filename(filename)
    document = SupplierDocument(
        rfq_id=rfq_id,
        communication_id=communication_id,
        supplier_id=supplier_id,
        filename=clean_name,
        content_type=content_type,
        size_bytes=len(payload),
        sha256=digest,
        storage_path=str(relative).replace("\\", "/"),
        kind=guess_kind(clean_name),
        text_status="stored",
    )
    db.add(document)
    db.flush()
    return StoredDocument(document=document, created=True)


def document_path(document: SupplierDocument) -> Path:
    return storage_root() / document.storage_path


def read_document_bytes(document: SupplierDocument) -> bytes:
    return document_path(document).read_bytes()
