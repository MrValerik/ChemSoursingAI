"""Извлечение текста документа для последующей проверки агентом.

Извлечение отделено от интерпретации: здесь только текст и признак того,
пригоден ли он для проверки. Скан без текстового слоя честно помечается как
`needs_ocr`, а не выдаётся за пустой документ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.models import SupplierDocument
from app.services.document_storage import (
    PDF,
    PLAIN_TEXT,
    read_document_bytes,
)
from app.services.search_trace import utc_now

# Верхняя граница: паспорт качества на десятки страниц не должен занимать
# память и контекст модели целиком.
_MAX_TEXT_CHARS = 60000


@dataclass
class ExtractedText:
    status: str
    text: str | None
    page_count: int | None
    error: str | None = None


def _normalize(text: str) -> str:
    """Схлопывает переносы PDF, сохраняя структуру строк таблиц."""
    text = text.replace("\x00", " ")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    kept = [line for line in lines if line]
    return "\n".join(kept)[:_MAX_TEXT_CHARS]


def extract_pdf_text(payload: bytes) -> ExtractedText:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - зависимость объявлена в requirements
        return ExtractedText(
            status="failed", text=None, page_count=None,
            error="Библиотека pypdf недоступна",
        )

    from io import BytesIO

    try:
        reader = PdfReader(BytesIO(payload))
        if reader.is_encrypted:
            # Пустой пароль открывает большинство «защищённых от печати» PDF.
            try:
                reader.decrypt("")
            except Exception:
                return ExtractedText(
                    status="unsupported", text=None, page_count=None,
                    error="PDF защищён паролем",
                )
        pages = reader.pages
        chunks = []
        for page in pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                # Одна нечитаемая страница не должна ронять весь документ.
                continue
        text = _normalize("\n".join(chunks))
    except Exception as exc:
        return ExtractedText(
            status="failed", text=None, page_count=None, error=str(exc)[:500]
        )

    page_count = len(pages)
    if len(text) < get_settings().document_min_text_chars:
        # Текстового слоя фактически нет: это скан, нужен OCR.
        return ExtractedText(
            status="needs_ocr",
            text=text or None,
            page_count=page_count,
            error="В PDF нет текстового слоя достаточного объёма",
        )
    return ExtractedText(status="extracted", text=text, page_count=page_count)


def extract_document_text(document: SupplierDocument) -> ExtractedText:
    """Возвращает текст документа по его типу."""
    try:
        payload = read_document_bytes(document)
    except OSError as exc:
        return ExtractedText(
            status="failed", text=None, page_count=None, error=str(exc)[:500]
        )

    if document.content_type == PDF:
        return extract_pdf_text(payload)
    if document.content_type == PLAIN_TEXT:
        text = _normalize(payload.decode("utf-8", errors="replace"))
        if not text:
            return ExtractedText(
                status="failed", text=None, page_count=None,
                error="Файл не содержит текста",
            )
        return ExtractedText(status="extracted", text=text, page_count=None)
    if document.content_type.startswith("image/"):
        return ExtractedText(
            status="needs_ocr", text=None, page_count=None,
            error="Изображение требует OCR",
        )
    return ExtractedText(
        status="unsupported", text=None, page_count=None,
        error=f"Тип {document.content_type} не поддерживается",
    )


def apply_extraction(document: SupplierDocument) -> SupplierDocument:
    """Извлекает текст и сохраняет результат в записи документа."""
    result = extract_document_text(document)
    document.text_status = result.status
    document.text_content = result.text
    document.page_count = result.page_count
    document.extraction_error = result.error
    document.extracted_at = utc_now()
    return document
