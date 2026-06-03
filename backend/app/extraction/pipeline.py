"""Оркестратор извлечения (раздел 5 ТЗ): LLM → валидаторы → fallback.

Стратегия:
  1. Всегда считаем rule-результат — это и baseline, и подтверждающий сигнал.
  2. Если доступна LLM — её structured output берём за основу.
  3. Поверх — детерминированные валидаторы (Incoterm/валюта/цена).
  4. Согласие LLM и правил повышает уверенность; расхождение — понижает.
  5. Нет LLM — отдаём rule-результат (конвейер не падает).
"""

from __future__ import annotations

from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.extraction.rule_extractor import extract_with_rules
from app.extraction.schema import ExtractedQuote
from app.extraction.validators import (
    validate_currency,
    validate_incoterm,
    validate_price,
)

# Базовые уровни уверенности для полей, пришедших из LLM.
_LLM_BASE_CONF = 0.8
_AGREE_CONF = 0.95
_DISAGREE_CONF = 0.5

_VALIDATED_FIELDS = {
    "price": validate_price,
    "currency": validate_currency,
    "incoterm": validate_incoterm,
}
_STRING_FIELDS = ("moq", "grade", "payment_terms", "lead_time")


def extract_quote(
    email_text: str,
    *,
    use_llm: bool = True,
    llm: LLMClient | None = None,
) -> ExtractedQuote:
    """Извлекает котировку из текста. llm можно подменить (тесты/моки)."""
    rules = extract_with_rules(email_text)

    if not use_llm:
        return rules

    try:
        client = llm or LLMClient()
        llm_dict = client.extract_quote(email_text)
    except LLMUnavailableError:
        # Модель недоступна — конвейер деградирует на правила.
        return rules

    return _merge(llm_dict, rules)


def _merge(llm_dict: dict, rules: ExtractedQuote) -> ExtractedQuote:
    """Сливает ответ LLM с rule-результатом, применяя валидаторы и confidence."""
    out = ExtractedQuote(method="llm+rules")
    confidence: dict[str, float] = {}

    # Поля с детерминированной проверкой.
    for fieldname, validator in _VALIDATED_FIELDS.items():
        llm_val = validator(llm_dict.get(fieldname))
        rule_val = getattr(rules, fieldname)
        value, conf = _reconcile(llm_val, rule_val)
        setattr(out, fieldname, value)
        if value is not None and value != "":
            confidence[fieldname] = conf

    # Строковые поля без жёсткой валидации: LLM в приоритете, иначе правила.
    for fieldname in _STRING_FIELDS:
        llm_val = llm_dict.get(fieldname)
        rule_val = getattr(rules, fieldname)
        value, conf = _reconcile(_clean_str(llm_val), rule_val)
        setattr(out, fieldname, value)
        if value:
            confidence[fieldname] = conf

    # Документы: True, если подтверждает хоть один источник.
    out.has_coa = bool(llm_dict.get("has_coa")) or rules.has_coa
    out.has_tds = bool(llm_dict.get("has_tds")) or rules.has_tds
    if out.has_coa:
        confidence["has_coa"] = 0.9
    if out.has_tds:
        confidence["has_tds"] = 0.9

    out.field_confidence = confidence
    return out


def _reconcile(llm_val, rule_val):
    """Возвращает (значение, уверенность) по согласию источников."""
    if llm_val is not None and rule_val is not None and rule_val != "":
        if _eq(llm_val, rule_val):
            return llm_val, _AGREE_CONF
        # Расхождение: при наличии валидного rule доверяем правилу (оно детерминировано).
        return rule_val, _DISAGREE_CONF
    if llm_val is not None:
        return llm_val, _LLM_BASE_CONF
    if rule_val is not None and rule_val != "":
        return rule_val, _LLM_BASE_CONF
    return None, 0.0


def _eq(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def _clean_str(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None
