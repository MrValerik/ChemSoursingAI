"""Контракт структурированного извлечения котировки (раздел 5 ТЗ).

ExtractedQuote — единый результат конвейера (и LLM-, и rule-пути). QUOTE_JSON_SCHEMA —
JSON-схема для structured output / function calling локальной LLM: модель обязана
вернуть строго эти поля, а не свободный текст.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExtractedQuote:
    """Результат извлечения. Поля повторяют сущность «Котировка» из ТЗ."""

    price: float | None = None
    currency: str | None = None
    incoterm: str | None = None
    moq: str | None = None
    grade: str | None = None
    payment_terms: str | None = None
    lead_time: str | None = None
    has_coa: bool = False
    has_tds: bool = False
    # Уверенность извлечения по каждому полю: {"price": 0.9, ...}
    field_confidence: dict[str, float] = field(default_factory=dict)
    # Каким путём получено: "llm" | "rules" | "llm+rules"
    method: str = "rules"

    def to_dict(self) -> dict:
        return asdict(self)


# JSON-схема для constrained decoding / function calling локальной LLM.
QUOTE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "price": {"type": ["number", "null"], "description": "Unit price as a number"},
        "currency": {"type": ["string", "null"], "description": "ISO currency code, e.g. USD"},
        "incoterm": {"type": ["string", "null"], "description": "Incoterm code, e.g. CIP/FCA/EXW"},
        "moq": {"type": ["string", "null"], "description": "Minimum order quantity with unit"},
        "grade": {"type": ["string", "null"], "description": "Product grade or purity"},
        "payment_terms": {"type": ["string", "null"]},
        "lead_time": {"type": ["string", "null"]},
        "has_coa": {"type": "boolean", "description": "Supplier offers Certificate of Analysis"},
        "has_tds": {"type": "boolean", "description": "Supplier offers Technical Data Sheet"},
    },
    "required": ["price", "currency", "incoterm", "moq", "has_coa", "has_tds"],
}
