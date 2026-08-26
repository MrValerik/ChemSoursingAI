"""Fallback-экстрактор на правилах (раздел 5 ТЗ).

Работает без LLM: собирает ExtractedQuote из текста ответа поставщика
детерминированными парсерами. Используется как запасной путь, когда локальная
модель недоступна, и как источник детерминированных валидаторов поверх LLM.
"""

from __future__ import annotations

from app.extraction.parsers import (
    parse_currency,
    parse_delivery_cost,
    parse_documents,
    parse_duty_cost,
    parse_grade,
    parse_hazmat,
    parse_incoterm,
    parse_landed_cost,
    parse_lead_time,
    parse_manufacturer,
    parse_moq,
    parse_origin_country,
    parse_packaging,
    parse_payment_terms,
    parse_price,
    parse_price_unit,
    parse_quoted_quantity,
    parse_total_price,
    parse_vat_cost,
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
    manufacturer = parse_manufacturer(text)
    origin_country = parse_origin_country(text)
    packaging = parse_packaging(text)
    price_unit = parse_price_unit(text)
    quoted_quantity = parse_quoted_quantity(text)
    total_price = parse_total_price(text)
    delivery_cost = parse_delivery_cost(text)
    duty_cost = parse_duty_cost(text)
    vat_cost = parse_vat_cost(text)
    landed_cost = parse_landed_cost(text)
    hazmat = parse_hazmat(text)
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
    put("manufacturer", manufacturer)
    put("origin_country", origin_country)
    put("packaging", packaging)
    put("price_unit", price_unit)
    put("quoted_quantity", quoted_quantity)
    put("total_price", total_price)
    put("delivery_cost", delivery_cost)
    put("duty_cost", duty_cost)
    put("vat_cost", vat_cost)
    put("landed_cost", landed_cost)
    put("is_hazmat", hazmat)
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
        manufacturer=manufacturer.value,
        origin_country=origin_country.value,
        packaging=packaging.value,
        price_unit=price_unit.value,
        quoted_quantity=quoted_quantity.value,
        total_price=total_price.value,
        delivery_cost=delivery_cost.value,
        duty_cost=duty_cost.value,
        vat_cost=vat_cost.value,
        landed_cost=landed_cost.value,
        cost_currency=(
            currency.value
            if any(
                parsed.value is not None
                for parsed in (
                    total_price,
                    delivery_cost,
                    duty_cost,
                    vat_cost,
                    landed_cost,
                )
            )
            else None
        ),
        is_hazmat=hazmat.value,
        has_coa=bool(coa.value),
        has_tds=bool(tds.value),
        field_confidence=confidence,
        method="rules",
    )
