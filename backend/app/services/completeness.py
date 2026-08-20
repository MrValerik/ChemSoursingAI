"""Контроль полноты котировки и confidence-пороги (функции 5, 7 ТЗ).

Чистая логика без БД и внешних зависимостей — легко тестируется.

Полнота (раздел 2 ТЗ, «Полнота котировок»): карточка считается полной, если
заполнены цена и валюта, базис поставки, MOQ, грейд, условия оплаты, срок и есть
спецификация (CoA или TDS).

Confidence-порог (раздел 5 ТЗ): поля с уверенностью ниже порога уходят на
ручную проверку либо в авто-дозапрос — отсюда правило эскалации LOW_CONFIDENCE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Обязательные для полноты поля (спецификация проверяется отдельно).
REQUIRED_FIELDS = (
    "price",
    "currency",
    "incoterm",
    "moq",
    "grade",
    "payment_terms",
    "lead_time",
)

# Порог уверенности извлечения по полю.
CONFIDENCE_THRESHOLD = 0.70


@dataclass
class CompletenessResult:
    is_complete: bool
    missing_fields: list[str] = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)


@dataclass
class AccumulatedQuoteResult:
    """Накопленные подтверждённые условия одного поставщика по одному RFQ."""

    quote: dict[str, Any]
    field_confidence: dict[str, float]
    completeness: CompletenessResult


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def evaluate_completeness(
    quote: Mapping[str, Any],
    field_confidence: Mapping[str, float] | None = None,
    *,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> CompletenessResult:
    """Оценивает полноту котировки.

    quote — словарь с коммерческими полями и флагами has_coa/has_tds.
    field_confidence — уверенность по полям (опционально).
    """
    missing: list[str] = [f for f in REQUIRED_FIELDS if _is_empty(quote.get(f))]

    # Спецификация: достаточно одного из документов.
    has_spec = bool(quote.get("has_coa")) or bool(quote.get("has_tds"))
    if not has_spec:
        missing.append("specification")

    low_conf: list[str] = []
    if field_confidence:
        low_conf = [
            name
            for name, conf in field_confidence.items()
            if name in REQUIRED_FIELDS
            and not _is_empty(quote.get(name))
            and conf is not None
            and conf < threshold
        ]

    is_complete = not missing and not low_conf
    return CompletenessResult(
        is_complete=is_complete,
        missing_fields=missing,
        low_confidence_fields=sorted(low_conf),
    )


def accumulate_quotations(
    quotations: Iterable[Any],
    *,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> AccumulatedQuoteResult:
    """Объединяет данные из нескольких ответов, не смешивая поставщиков.

    Вызывающий код передаёт только котировки одного поставщика и одного RFQ.
    Более позднее значение заменяет раннее, если оно не менее надёжно. Поэтому
    случайный ответ с низкой уверенностью не стирает ранее подтверждённое поле.
    CoA/TDS накапливаются логическим OR.
    """

    merged: dict[str, Any] = {
        "price": None,
        "currency": None,
        "incoterm": None,
        "moq": None,
        "grade": None,
        "payment_terms": None,
        "lead_time": None,
        "has_coa": False,
        "has_tds": False,
    }
    confidence: dict[str, float] = {}

    for quotation in quotations:
        values = quotation if isinstance(quotation, Mapping) else vars(quotation)
        raw_confidence = values.get("field_confidence") or {}
        for name in REQUIRED_FIELDS:
            value = values.get(name)
            if _is_empty(value):
                continue
            new_confidence = raw_confidence.get(name)
            current_confidence = confidence.get(name)
            if (
                _is_empty(merged.get(name))
                or new_confidence is None
                or current_confidence is None
                or new_confidence >= current_confidence
            ):
                merged[name] = value
                if new_confidence is None:
                    confidence.pop(name, None)
                else:
                    confidence[name] = float(new_confidence)
        merged["has_coa"] = bool(merged["has_coa"] or values.get("has_coa"))
        merged["has_tds"] = bool(merged["has_tds"] or values.get("has_tds"))

    completeness = evaluate_completeness(
        merged,
        confidence,
        threshold=threshold,
    )
    return AccumulatedQuoteResult(
        quote=merged,
        field_confidence=confidence,
        completeness=completeness,
    )
