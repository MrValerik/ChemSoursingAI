"""Документы поставщика (CoA, TDS, паспорт качества) и их разбор.

Файл хранится в контуре заказчика, а извлечённый текст — отдельно от машинной
интерпретации: агент-проверяющий работает только с сохранённым текстом, а его
выводы обязаны ссылаться на дословные цитаты из него.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.communication import Communication
    from app.models.rfq import RFQ
    from app.models.supplier import Supplier


class SupplierDocument(Base, TimestampMixin):
    """Сохранённый файл поставщика и результат его извлечения в текст."""

    __tablename__ = "supplier_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id", ondelete="SET NULL"), index=True, default=None
    )
    communication_id: Mapped[int | None] = mapped_column(
        ForeignKey("communications.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), index=True, default=None
    )

    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Хеш содержимого: дедупликация повторных писем и проверка целостности.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # Путь относительно ATTACHMENT_STORAGE_DIR; имя файла задаёт система,
    # а не отправитель, поэтому обход каталогов невозможен.
    storage_path: Mapped[str] = mapped_column(String(500))

    # coa / tds / msds / other — предварительная догадка по имени файла.
    kind: Mapped[str] = mapped_column(String(32), default="other", index=True)
    # stored / extracted / needs_ocr / unsupported / failed
    text_status: Mapped[str] = mapped_column(
        String(32), default="stored", index=True
    )
    text_content: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    extraction_error: Mapped[str | None] = mapped_column(Text, default=None)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Итог независимой проверки паспорта: заполняется агентом document_verifier.
    verification: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=None
    )

    rfq: Mapped["RFQ | None"] = relationship()
    communication: Mapped["Communication | None"] = relationship()
    supplier: Mapped["Supplier | None"] = relationship()
