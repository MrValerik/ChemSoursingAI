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
    manufacturer: str | None = None
    origin_country: str | None = None
    packaging: str | None = None
    price_unit: str | None = None
    quoted_quantity: str | None = None
    total_price: float | None = None
    delivery_cost: float | None = None
    duty_cost: float | None = None
    vat_cost: float | None = None
    landed_cost: float | None = None
    cost_currency: str | None = None
    is_hazmat: bool | None = None
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
        "manufacturer": {"type": ["string", "null"]},
        "origin_country": {"type": ["string", "null"]},
        "packaging": {"type": ["string", "null"]},
        "price_unit": {"type": ["string", "null"], "description": "Unit used for the unit price, e.g. kg"},
        "quoted_quantity": {"type": ["string", "null"], "description": "Quantity covered by the quotation, with unit"},
        "total_price": {"type": ["number", "null"], "description": "Explicitly stated purchase total; never calculate it"},
        "delivery_cost": {"type": ["number", "null"], "description": "Explicitly stated freight or delivery cost"},
        "duty_cost": {"type": ["number", "null"], "description": "Explicitly stated import duty amount"},
        "vat_cost": {"type": ["number", "null"], "description": "Explicitly stated VAT amount"},
        "landed_cost": {"type": ["number", "null"], "description": "Explicitly stated landed total; never calculate it"},
        "cost_currency": {"type": ["string", "null"], "description": "ISO currency shared by stated cost totals"},
        "is_hazmat": {"type": ["boolean", "null"], "description": "Whether supplier explicitly marks shipment as hazardous"},
        "has_coa": {"type": "boolean", "description": "Supplier offers Certificate of Analysis"},
        "has_tds": {"type": "boolean", "description": "Supplier offers Technical Data Sheet"},
    },
    "required": [
        "price", "currency", "incoterm", "moq", "grade", "payment_terms",
        "lead_time", "manufacturer", "origin_country", "packaging",
        "price_unit", "quoted_quantity", "total_price", "delivery_cost",
        "duty_cost", "vat_cost", "landed_cost", "cost_currency", "is_hazmat",
        "has_coa", "has_tds"
    ],
}
