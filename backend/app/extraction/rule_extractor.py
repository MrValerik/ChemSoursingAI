"""Fallback-экстрактор на правилах (раздел 5 ТЗ).

Работает без LLM: собирает ExtractedQuote из текста ответа поставщика
детерминированными парсерами. Используется как запасной путь, когда локальная
модель недоступна, и как источник детерминированных валидаторов поверх LLM.
"""

from __future__ import annotations

from app.extraction.parsers import (
    parse_currency,
    parse_documents,
    parse_grade,
    parse_incoterm,
    parse_lead_time,
    parse_moq,
    parse_payment_terms,
    parse_price,
)
from app.extraction.schema import ExtractedQuote


def extract_with_rules(text: str) -> ExtractedQuote:
    """Извлекает котировку из текста письма набором парсеров."""
    price = parse_price(text)
    currency = parse_currency(text)
    incoterm = parse_incoterm(text)
    moq = parse_moq(text)
    grade = parse_grade(text)
    payment = parse_payment_terms(text)
    lead = parse_lead_time(text)
    coa, tds = parse_documents(text)

    confidence: dict[str, float] = {}

    def put(name: str, parsed) -> None:
        # Уверенность фиксируем только для реально найденных полей.
        if parsed.value is not None and parsed.value != "":
            confidence[name] = parsed.confidence

    put("price", price)
    put("currency", currency)
    put("incoterm", incoterm)
    put("moq", moq)
    put("grade", grade)
    put("payment_terms", payment)
    put("lead_time", lead)
    # Документы: уверенность фиксируем только при положительном обнаружении.
    if coa.value:
        confidence["has_coa"] = coa.confidence
    if tds.value:
        confidence["has_tds"] = tds.confidence

    return ExtractedQuote(
        price=price.value,
        currency=currency.value,
        incoterm=incoterm.value,
        moq=moq.value,
        grade=grade.value,
        payment_terms=payment.value,
        lead_time=lead.value,
        has_coa=bool(coa.value),
        has_tds=bool(tds.value),
        field_confidence=confidence,
        method="rules",
    )
