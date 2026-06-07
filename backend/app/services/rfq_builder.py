"""Генерация стандартизированного RFQ (раздел 3, функция 2 ТЗ).

Единый шаблон под выбранные базисы поставки (Incoterms) с обязательным
перечнем документов (CoA, TDS). Цель метрики «100% RFQ по единому шаблону»
обеспечивается тем, что текст собирается детерминированно из структуры, а не
пишется вручную.

Язык письма — английский: основная переписка с поставщиками (преимущественно
из КНР) ведётся на английском.
"""

from __future__ import annotations

from dataclasses import dataclass

# Человекочитаемые места поставки под каждый базис (целевые для MVP).
INCOTERM_PLACES = {
    "CIP": "CIP Moscow, Russia",
    "FCA": "FCA Shanghai, China",
    "EXW": "EXW (seller's works)",
}

# Документы, обязательные к запросу в каждом RFQ.
REQUIRED_DOCUMENTS = ["Certificate of Analysis (CoA)", "Technical Data Sheet (TDS)"]


@dataclass
class RFQInput:
    """Входные параметры для генерации RFQ."""

    cas: str
    name: str
    incoterms: list[str]
    purity: str | None = None
    application: str | None = None
    volume: str | None = None
    target_price: float | None = None
    currency: str = "USD"


class UnsupportedIncotermError(ValueError):
    """Передан базис вне поддерживаемого набора (CIP/FCA/EXW)."""


def _validate_incoterms(incoterms: list[str]) -> list[str]:
    if not incoterms:
        raise UnsupportedIncotermError("incoterms list is empty")
    normalized = [i.strip().upper() for i in incoterms]
    unsupported = [i for i in normalized if i not in INCOTERM_PLACES]
    if unsupported:
        raise UnsupportedIncotermError(f"unsupported incoterms: {unsupported}")
    return normalized


def build_rfq(data: RFQInput) -> dict:
    """Собирает RFQ: структурированные поля запроса (для рассылки/трекинга) +
    готовый текст письма под выбранные базисы.
    """
    incoterms = _validate_incoterms(data.incoterms)

    fields = {
        "substance": data.name,
        "cas": data.cas,
        "purity": data.purity or "to be specified by supplier",
        "application": data.application,
        "quantity": data.volume or "to be confirmed",
        "incoterms": incoterms,
        "delivery_terms": [INCOTERM_PLACES[i] for i in incoterms],
        "required_documents": REQUIRED_DOCUMENTS,
        "requested_quote_fields": [
            "unit price",
            "currency",
            "incoterm / delivery basis",
            "MOQ",
            "grade",
            "payment terms",
            "lead time",
            "CoA availability",
            "TDS availability",
        ],
    }

    return {
        "subject": _build_subject(data),
        "body": _build_body(data, incoterms),
        "fields": fields,
    }


def _build_subject(data: RFQInput) -> str:
    return f"RFQ: {data.name} (CAS {data.cas})"


def _build_body(data: RFQInput, incoterms: list[str]) -> str:
    lines: list[str] = []
    lines.append("Dear Supplier,")
    lines.append("")
    lines.append(
        "We are interested in purchasing the following chemical raw material "
        "and kindly request your quotation."
    )
    lines.append("")
    lines.append("Product details:")
    lines.append(f"  - Substance: {data.name}")
    lines.append(f"  - CAS No.: {data.cas}")
    if data.purity:
        lines.append(f"  - Purity / Grade: {data.purity}")
    if data.application:
        lines.append(f"  - Application: {data.application}")
    lines.append(f"  - Quantity: {data.volume or 'to be confirmed'}")
    lines.append("")

    lines.append("Please quote on the following delivery basis (Incoterms 2020):")
    for code in incoterms:
        lines.append(f"  - {code} — {INCOTERM_PLACES[code]}")
    lines.append("")

    lines.append("Please include in your offer:")
    for f in [
        "Unit price and currency",
        "Delivery basis (per Incoterm above)",
        "Minimum Order Quantity (MOQ)",
        "Product grade",
        "Payment terms",
        "Lead time",
    ]:
        lines.append(f"  - {f}")
    lines.append("")

    lines.append("Required documents (please attach):")
    for doc in REQUIRED_DOCUMENTS:
        lines.append(f"  - {doc}")
    lines.append("")

    lines.append("We look forward to your prompt reply.")
    lines.append("Best regards,")
    lines.append("Procurement Department")
    return "\n".join(lines)
