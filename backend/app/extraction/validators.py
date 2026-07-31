"""Детерминированные валидаторы поверх ответа LLM (раздел 5 ТЗ).

То, что можно проверить правилом, не доверяется модели: Incoterm — по словарю,
валюта — по справочнику, цена — положительное число, CAS — по контрольной сумме.
Валидатор возвращает (очищенное значение, прошло ли проверку).
"""

from __future__ import annotations

from app.extraction.parsers import CURRENCY_CODES, INCOTERMS, _CURRENCY_ALIASES
from app.services.cas import is_valid_cas

_KNOWN_CURRENCIES = {_CURRENCY_ALIASES.get(c, c) for c in CURRENCY_CODES}


def validate_incoterm(value) -> str | None:
    if not value:
        return None
    code = str(value).strip().upper()
    return code if code in INCOTERMS else None


def validate_currency(value) -> str | None:
    if not value:
        return None
    code = str(value).strip().upper()
    code = _CURRENCY_ALIASES.get(code, code)
    return code if code in _KNOWN_CURRENCIES else None


def validate_price(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def validate_cas(value) -> str | None:
    if value and is_valid_cas(str(value)):
        return str(value).strip()
    return None
