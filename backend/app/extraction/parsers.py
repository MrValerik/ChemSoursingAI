"""Детерминированные парсеры полей котировки (раздел 5 ТЗ).

Чистые функции без БД/сети — то, что можно проверить правилом, не отдаётся
LLM «на угадывание». Каждый парсер возвращает (значение, уверенность).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Parsed(Generic[T]):
    value: T | None
    confidence: float  # 0..1


# --- Валюты ---
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "¥": "CNY", "£": "GBP", "₽": "RUB"}
CURRENCY_CODES = {"USD", "EUR", "CNY", "RMB", "GBP", "RUB", "JPY"}
_CURRENCY_ALIASES = {"RMB": "CNY"}

# --- Incoterms (полный набор для распознавания; продукт целится в CIP/FCA/EXW) ---
INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")

# Числовой токен: 12, 12.5, 1,250.00 (неперехватывающая группа — атомарный токен)
_NUMBER = r"(?:\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"


def _to_float(raw: str) -> float | None:
    cleaned = raw.replace(" ", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_currency(text: str) -> Parsed[str]:
    """Извлекает валюту по коду (USD/EUR/CNY…) или символу ($/€/¥)."""
    up = text.upper()
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", up):
            return Parsed(_CURRENCY_ALIASES.get(code, code), 0.95)
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return Parsed(code, 0.85)
    return Parsed(None, 0.0)


def parse_price(text: str) -> Parsed[float]:
    """Извлекает цену за единицу. Ищет число рядом с валютой или с '/kg', 'per kg'.

    Возвращает первое подходящее значение (обычно поставщики дают одну цену).
    """
    # Паттерн: [валюта] число [/ед] либо число [валюта] [/ед]
    patterns = [
        rf"(?:USD|EUR|CNY|RMB|GBP|RUB|\$|€|¥|£|₽)\s*({_NUMBER})",
        rf"({_NUMBER})\s*(?:USD|EUR|CNY|RMB|GBP|RUB|\$|€|¥|£|₽)",
        rf"({_NUMBER})\s*(?:/\s*(?:kg|g|mt|ton|tonne|l|lb)|per\s+(?:kg|g|mt|ton|tonne|l|lb))",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = _to_float(m.group(1))
            if val is not None:
                # Цена рядом с валютой надёжнее, чем просто «число/kg».
                conf = 0.9 if i < 2 else 0.7
                return Parsed(val, conf)
    return Parsed(None, 0.0)


def parse_incoterm(text: str) -> Parsed[str]:
    """Распознаёт базис поставки (Incoterms) по словарю."""
    up = text.upper()
    for code in INCOTERMS:
        if re.search(rf"\b{code}\b", up):
            return Parsed(code, 0.9)
    return Parsed(None, 0.0)


def parse_moq(text: str) -> Parsed[str]:
    """Извлекает минимальный заказ (MOQ): 'MOQ 25 kg', 'min order 1 ton'."""
    patterns = [
        rf"MOQ(?:\s+is)?(?:\s+in)?[:\s]*"
        rf"({_NUMBER}\s*(?:kg|g|mt|ton|tonne|l|lb|drum|bag)s?)",
        rf"min(?:imum)?\.?\s*order(?:\s*quantity)?[:\s]*({_NUMBER}\s*(?:kg|g|mt|ton|tonne|l|lb|drum|bag)s?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return Parsed(m.group(1).strip(), 0.9)
    return Parsed(None, 0.0)


def parse_documents(text: str) -> tuple[Parsed[bool], Parsed[bool]]:
    """Определяет только положительное предоставление CoA и TDS.

    Простое упоминание недостаточно: ``please provide CoA`` — это запрос
    покупателя, а не доказательство наличия документа у поставщика.
    """
    low = text.lower()
    positive = (
        r"(?:attached|enclosed|included|available|provided|"
        r"can\s+be\s+provided|can\s+provide|will\s+provide|"
        r"(?:we|i)\s+(?:have|provide)|yes)"
    )
    coa = r"(?:\bcoa\b|certificate\s+of\s+analysis)"
    tds = r"(?:\btds\b|technical\s+data\s+sheet|spec(?:ification)?\s+sheet)"
    shared_positive = bool(
        re.search(rf"{positive}[^\n.]{{0,80}}{coa}[^\n.]{{0,50}}{tds}", low)
        or re.search(rf"{positive}[^\n.]{{0,80}}{tds}[^\n.]{{0,50}}{coa}", low)
        or re.search(rf"{coa}[^\n.]{{0,50}}{tds}[^\n.]{{0,50}}{positive}", low)
        or re.search(rf"{tds}[^\n.]{{0,50}}{coa}[^\n.]{{0,50}}{positive}", low)
    )
    has_coa = bool(
        re.search(rf"{coa}\s*(?:is|are|:)?\s*{positive}", low)
        or re.search(rf"{positive}\s+(?:the\s+)?{coa}", low)
        or shared_positive
    )
    has_tds = bool(
        re.search(rf"{tds}\s*(?:is|are|:)?\s*{positive}", low)
        or re.search(rf"{positive}\s+(?:the\s+)?{tds}", low)
        or shared_positive
    )
    return (
        Parsed(has_coa, 0.9 if has_coa else 0.5),
        Parsed(has_tds, 0.9 if has_tds else 0.5),
    )


def parse_lead_time(text: str) -> Parsed[str]:
    """Срок поставки: 'lead time 15 days', 'delivery in 2 weeks'."""
    patterns = [
        rf"lead\s*time[:\s]*({_NUMBER}\s*(?:day|week|month)s?)",
        rf"deliver(?:y|ed)?\s*(?:in|within)?[:\s]*({_NUMBER}\s*(?:day|week|month)s?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return Parsed(m.group(1).strip(), 0.85)
    return Parsed(None, 0.0)


def parse_payment_terms(text: str) -> Parsed[str]:
    """Условия оплаты: 'T/T', 'L/C', '30% deposit'."""
    m = re.search(r"\b(T/T|L/C|D/P|D/A)\b", text, flags=re.IGNORECASE)
    if m:
        return Parsed(m.group(1).upper(), 0.85)
    m = re.search(
        rf"({_NUMBER}\s*%\s*(?:deposit|advance|in advance))",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return Parsed(m.group(1).strip(), 0.75)
    return Parsed(None, 0.0)


def parse_grade(text: str) -> Parsed[str]:
    """Грейд/чистота: 'USP grade', '99.5% purity', 'industrial grade'."""
    m = re.search(
        r"\b(USP|BP|EP|ACS|HPLC|food|pharma(?:ceutical)?|industrial|"
        r"technical|reagent)\s*grade\b",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return Parsed(m.group(0).strip(), 0.85)
    m = re.search(rf"({_NUMBER}\s*%)\s*(?:purity|min|assay)", text, flags=re.IGNORECASE)
    if m:
        return Parsed(m.group(1).strip(), 0.75)
    return Parsed(None, 0.0)


_QUANTITY_UNITS = r"kg|g|mt|ton|tonne|l|lb|drum|bag|item|unit|pc|pcs"
_CURRENCY_TOKEN = r"USD|EUR|CNY|RMB|GBP|RUB|JPY|\$|€|¥|£|₽"
_MONEY_NUMBER = r"(?:\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def parse_price_unit(text: str) -> Parsed[str]:
    """Единица цены из конструкции ``USD 12.5/kg`` или ``per kg``."""
    patterns = [
        rf"(?:{_CURRENCY_TOKEN})\s*{_MONEY_NUMBER}\s*"
        rf"(?:/\s*|per\s+)({_QUANTITY_UNITS})\b",
        rf"{_MONEY_NUMBER}\s*(?:{_CURRENCY_TOKEN})\s*"
        rf"(?:/\s*|per\s+)({_QUANTITY_UNITS})\b",
        rf"price\s+per\s+({_QUANTITY_UNITS})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return Parsed(match.group(1).lower(), 0.9)
    return Parsed(None, 0.0)


def parse_quoted_quantity(text: str) -> Parsed[str]:
    """Объём, на который поставщик дал цену; MOQ сюда не подменяется."""
    patterns = [
        (
            rf"(?:quoted\s+quantity|quantity\s+quoted|offer\s+quantity|quantity)"
            rf"[:\s]*({_NUMBER}\s*(?:{_QUANTITY_UNITS})s?)\b"
        ),
        (
            rf"(?:{_CURRENCY_TOKEN})\s*{_MONEY_NUMBER}\s*"
            rf"(?:/\s*|per\s+)(?:{_QUANTITY_UNITS})\b[^\n]{{0,100}}?"
            rf"\bfor\s+({_NUMBER}\s*(?:{_QUANTITY_UNITS})s?)\b"
        ),
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return Parsed(match.group(1).strip(), 0.85)
    return Parsed(None, 0.0)


def parse_explicit_price_offers(text: str) -> list[dict[str, object]]:
    """Читает несколько явно размеченных ценовых строк одного письма.

    Скалярный structured output модели не может сохранить одновременно FOB и
    CIP. Поэтому две строки ``Price: USD ...`` превращаются в две котировки, а
    не в случайную смесь первой цены и последнего Incoterm.
    """
    offers: list[dict[str, object]] = []
    price_pattern = re.compile(
        rf"(?:\bprice\s*[:=]?\s*)?(?P<currency>{_CURRENCY_TOKEN})\s*"
        rf"(?P<price>{_MONEY_NUMBER})\s*(?:/\s*|per\s+)"
        rf"(?P<unit>{_QUANTITY_UNITS})\b",
        flags=re.IGNORECASE,
    )
    quantity_pattern = re.compile(
        rf"\bfor\s+(?P<quantity>{_NUMBER}\s*(?:{_QUANTITY_UNITS})s?)\b",
        flags=re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        match = price_pattern.search(raw_line)
        if match is None:
            continue
        price = _to_float(match.group("price"))
        if price is None:
            continue
        raw_currency = match.group("currency").upper()
        currency = CURRENCY_SYMBOLS.get(
            raw_currency,
            _CURRENCY_ALIASES.get(raw_currency, raw_currency),
        )
        incoterm = parse_incoterm(raw_line).value
        quantity_match = quantity_pattern.search(raw_line)
        offers.append(
            {
                "price": price,
                "currency": currency,
                "incoterm": incoterm,
                "price_unit": match.group("unit").lower(),
                "quoted_quantity": (
                    quantity_match.group("quantity").strip()
                    if quantity_match is not None
                    else None
                ),
            }
        )
    return offers


def parse_packaging(text: str) -> Parsed[str]:
    """Фасовка только после явной метки packaging/packing/packed in."""
    patterns = [
        r"(?:packaging|packing)\s*:\s*([^\n.;]{1,120})",
        r"packed\s+in\s+([^\n.;]{1,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return Parsed(match.group(1).strip(), 0.85)
    return Parsed(None, 0.0)


def _parse_labeled_money(text: str, label_pattern: str) -> Parsed[float]:
    patterns = [
        rf"(?:{label_pattern})\s*[:=]?\s*(?:{_CURRENCY_TOKEN})\s*({_MONEY_NUMBER})(?!\d|[.,]\d)",
        rf"(?:{label_pattern})\s*[:=]?\s*({_MONEY_NUMBER})(?!\d|[.,]\d)\s*(?:{_CURRENCY_TOKEN})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            amount = _to_float(match.group(1))
            if amount is not None:
                return Parsed(amount, 0.9)
    return Parsed(None, 0.0)


def parse_total_price(text: str) -> Parsed[float]:
    return _parse_labeled_money(text, r"total(?:\s+(?:price|amount|cost))?")


def parse_delivery_cost(text: str) -> Parsed[float]:
    return _parse_labeled_money(text, r"(?:freight|shipping|delivery)(?:\s+cost)?")


def parse_duty_cost(text: str) -> Parsed[float]:
    return _parse_labeled_money(text, r"(?:import\s+)?dut(?:y|ies)")


def parse_vat_cost(text: str) -> Parsed[float]:
    return _parse_labeled_money(text, r"VAT")


def parse_landed_cost(text: str) -> Parsed[float]:
    return _parse_labeled_money(text, r"(?:total\s+)?landed\s+cost")


def parse_manufacturer(text: str) -> Parsed[str]:
    match = re.search(
        r"(?:manufacturer|manufactured\s+by)\s*:\s*([^\n.;]{2,255})",
        text,
        flags=re.IGNORECASE,
    )
    return Parsed(match.group(1).strip(), 0.85) if match else Parsed(None, 0.0)


def parse_origin_country(text: str) -> Parsed[str]:
    match = re.search(
        r"(?:country\s+of\s+origin|origin\s+country)\s*:\s*([^\n.;]{2,120})",
        text,
        flags=re.IGNORECASE,
    )
    return Parsed(match.group(1).strip(), 0.85) if match else Parsed(None, 0.0)


def parse_hazmat(text: str) -> Parsed[bool]:
    if re.search(r"\b(?:non[-\s]?hazardous|not\s+hazmat)\b", text, re.IGNORECASE):
        return Parsed(False, 0.9)
    if re.search(r"\b(?:hazmat|hazardous|dangerous\s+goods?)\b", text, re.IGNORECASE):
        return Parsed(True, 0.85)
    return Parsed(None, 0.0)
