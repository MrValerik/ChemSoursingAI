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
    validate_nonnegative_amount,
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
    "total_price": validate_nonnegative_amount,
    "delivery_cost": validate_nonnegative_amount,
    "duty_cost": validate_nonnegative_amount,
    "vat_cost": validate_nonnegative_amount,
    "landed_cost": validate_nonnegative_amount,
    "cost_currency": validate_currency,
}
_EXPLICIT_AMOUNT_FIELDS = {
    "total_price",
    "delivery_cost",
    "duty_cost",
    "vat_cost",
    "landed_cost",
}
_STRING_FIELDS = (
    "moq",
    "grade",
    "payment_terms",
    "lead_time",
    "manufacturer",
    "origin_country",
    "packaging",
    "price_unit",
    "quoted_quantity",
)


def extract_quote(
    email_text: str,
    *,
    use_llm: bool = True,
    llm: LLMClient | None = None,
    system_prompt: str | None = None,
    additional_instructions: str | None = None,
) -> ExtractedQuote:
    """Извлекает котировку из текста. llm можно подменить (тесты/моки)."""
    rules = extract_with_rules(email_text)

    if not use_llm:
        return rules

    try:
        client = llm or LLMClient()
        if system_prompt or additional_instructions:
            llm_dict = client.extract_quote(
                email_text,
                system_prompt=system_prompt,
                additional_instructions=additional_instructions,
            )
        else:
            # Сохраняем простой контракт для тестовых/альтернативных LLM-клиентов.
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
        if fieldname in _EXPLICIT_AMOUNT_FIELDS and rule_val is None:
            # Денежная разбивка влияет на сравнение предложений. Одного вывода
            # модели недостаточно: сумма должна стоять рядом с явной меткой в
            # исходном письме и подтверждаться детерминированным парсером.
            value, conf = None, 0.0
        elif fieldname == "cost_currency" and not any(
            getattr(rules, name) is not None for name in _EXPLICIT_AMOUNT_FIELDS
        ):
            value, conf = None, 0.0
        else:
            value, conf = _reconcile(llm_val, rule_val)
        setattr(out, fieldname, value)
        if value is not None and value != "":
            confidence[fieldname] = conf

    # Строковые поля без жёсткой валидации: LLM в приоритете, иначе правила.
    for fieldname in _STRING_FIELDS:
        llm_val = llm_dict.get(fieldname)
        rule_val = getattr(rules, fieldname)
        cleaned_llm = _clean_str(llm_val)
        if fieldname == "lead_time":
            cleaned_llm = validate_lead_time_value(cleaned_llm)
        value, conf = _reconcile(cleaned_llm, rule_val)
        setattr(out, fieldname, value)
        if value:
            confidence[fieldname] = conf

    # Наличие документа — consequential fact. Модель не может повысить его из
    # одного упоминания в процитированном запросе; здесь нужен положительный
    # детерминированный маркер, а реальные вложения добавляет email workflow.
    out.has_coa = rules.has_coa
    out.has_tds = rules.has_tds
    if out.has_coa:
        confidence["has_coa"] = 0.9
    if out.has_tds:
        confidence["has_tds"] = 0.9

    llm_hazmat = llm_dict.get("is_hazmat")
    rule_hazmat = rules.is_hazmat
    if llm_hazmat is True or rule_hazmat is True:
        out.is_hazmat = True
        confidence["is_hazmat"] = (
            _AGREE_CONF if llm_hazmat is True and rule_hazmat is True else _DISAGREE_CONF
        )
    elif llm_hazmat is False or rule_hazmat is False:
        out.is_hazmat = False
        confidence["is_hazmat"] = (
            _AGREE_CONF
            if llm_hazmat is False and rule_hazmat is False
            else _LLM_BASE_CONF
        )

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


def validate_lead_time_value(value: str | None) -> str | None:
    """Не подменяет срок наличием товара на складе."""
    if value is None:
        return None
    normalized = " ".join(value.casefold().split()).strip(" .!:")
    availability_only = {
        "in stock",
        "available",
        "available now",
        "ready stock",
        "stock available",
    }
    return None if normalized in availability_only else value
