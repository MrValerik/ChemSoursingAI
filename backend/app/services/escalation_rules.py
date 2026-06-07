"""Правила эскалации нестандартных кейсов специалисту (функция 9 ТЗ).

Чистая логика: по котировке и результату полноты определяем, нужна ли
эскалация и по какой причине. Соответствует разделу 7 ТЗ (причины:
грейд/логистика/дефицит/кастом-синтез) плюс LOW_CONFIDENCE из раздела 5.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.models.enums import EscalationReason
from app.services.completeness import CompletenessResult

# Ключевые слова в тексте/полях, указывающие на нестандартный кейс.
_CUSTOM_SYNTHESIS_HINTS = ("custom synthesis", "custom-synthesis", "made to order", "на заказ")
_SHORTAGE_HINTS = ("out of stock", "sold out", "no stock", "shortage", "нет в наличии")
_HAZARD_HINTS = ("dangerous goods", "hazardous", "imo", "un number", "опасный груз")


def _contains(text: str, hints) -> bool:
    low = text.lower()
    return any(h in low for h in hints)


def detect_escalation(
    quote: Mapping[str, Any],
    completeness: CompletenessResult,
    *,
    free_text: str = "",
) -> EscalationReason | None:
    """Возвращает причину эскалации или None, если кейс штатный.

    Приоритет: явные доменные сигналы (кастом-синтез/дефицит/логистика) выше,
    затем низкая уверенность извлечения.
    """
    haystack = " ".join(
        str(quote.get(k, "")) for k in ("grade", "payment_terms", "lead_time")
    ) + " " + free_text

    if _contains(haystack, _CUSTOM_SYNTHESIS_HINTS):
        return EscalationReason.CUSTOM_SYNTHESIS
    if _contains(haystack, _SHORTAGE_HINTS):
        return EscalationReason.SHORTAGE
    if _contains(haystack, _HAZARD_HINTS):
        return EscalationReason.LOGISTICS

    if completeness.low_confidence_fields:
        return EscalationReason.LOW_CONFIDENCE

    return None
