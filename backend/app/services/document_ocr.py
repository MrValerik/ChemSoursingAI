"""Распознавание сканов паспортов качества (OCR) на CPU.

Используется только тогда, когда текстового слоя в файле нет. OCR — источник
менее надёжного текста, чем текстовый слой PDF, поэтому его результат
помечается отдельным статусом: проверяющий агент и человек должны знать,
что цитаты получены распознаванием.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str | None
    page_count: int | None
    error: str | None = None


def ocr_available() -> tuple[bool, str | None]:
    """Проверяет наличие Tesseract и конвертера PDF без запуска распознавания."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "Библиотека pytesseract не установлена"
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        return False, "Библиотека pdf2image не установлена"
    try:
        import pytesseract as engine

        engine.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - зависит от системного пакета
        return False, f"Tesseract недоступен: {exc}"
    return True, None


def ocr_pdf(payload: bytes) -> OcrResult:
    """Распознаёт первые страницы PDF-скана."""
    settings = get_settings()
    available, reason = ocr_available()
    if not available:
        return OcrResult(text=None, page_count=None, error=reason)

    import pdf2image
    import pytesseract

    try:
        images = pdf2image.convert_from_bytes(
            payload,
            dpi=settings.ocr_dpi,
            first_page=1,
            last_page=settings.ocr_max_pages,
        )
    except Exception as exc:
        return OcrResult(
            text=None, page_count=None,
            error=f"Не удалось преобразовать PDF в изображения: {exc}"[:400],
        )

    chunks: list[str] = []
    for image in images:
        try:
            chunks.append(
                pytesseract.image_to_string(
                    image, lang=settings.ocr_languages
                )
            )
        except Exception as exc:  # pragma: no cover - сбой одной страницы
            logger.warning("OCR страницы не выполнен: %s", exc)
            continue

    text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    if not text:
        return OcrResult(
            text=None, page_count=len(images),
            error="Распознавание не дало текста",
        )
    return OcrResult(text=text, page_count=len(images))


def ocr_image(payload: bytes) -> OcrResult:
    """Распознаёт одиночное изображение (фото или скан страницы)."""
    available, reason = ocr_available()
    if not available:
        return OcrResult(text=None, page_count=None, error=reason)

    from io import BytesIO

    import pytesseract
    from PIL import Image

    try:
        with Image.open(BytesIO(payload)) as image:
            text = pytesseract.image_to_string(
                image, lang=get_settings().ocr_languages
            )
    except Exception as exc:
        return OcrResult(
            text=None, page_count=None, error=f"OCR не выполнен: {exc}"[:400]
        )
    text = text.strip()
    if not text:
        return OcrResult(
            text=None, page_count=1, error="Распознавание не дало текста"
        )
    return OcrResult(text=text, page_count=1)
