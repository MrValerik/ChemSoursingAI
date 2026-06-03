"""Контроль полноты котировки и confidence-пороги (функции 5, 7 ТЗ).

Чистая логика без БД и внешних зависимостей — легко тестируется.

Полнота (раздел 2 ТЗ, «Полнота котировок»): карточка считается полной, если
заполнены цена, базис поставки, MOQ и есть спецификация (CoA или TDS).

Confidence-порог (раздел 5 ТЗ): поля с уверенностью ниже порога уходят на
ручную проверку либо в авто-дозапрос — отсюда правило эскалации LOW_CONFIDENCE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# Обязательные для полноты поля (спецификация проверяется отдельно).
REQUIRED_FIELDS = ("price", "incoterm", "moq")

# Порог уверенности извлечения по полю.
CONFIDENCE_THRESHOLD = 0.70


@dataclass
class CompletenessResult:
    is_complete: bool
    missing_fields: list[str] = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)


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

    quote — словарь с полями price, incoterm, moq, has_coa, has_tds.
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
            if conf is not None and conf < threshold
        ]

    is_complete = not missing and not low_conf
    return CompletenessResult(
        is_complete=is_complete,
        missing_fields=missing,
        low_confidence_fields=sorted(low_conf),
    )
