"""Тесты детерминированных парсеров полей котировки."""

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


def test_price_currency_prefix():
    assert parse_price("Our price is USD 12.50/kg").value == 12.5


def test_price_currency_suffix():
    assert parse_price("Price: 8.00 EUR per kg").value == 8.0


def test_price_symbol():
    assert parse_price("$15/kg CIF").value == 15.0


def test_price_thousands():
    assert parse_price("USD 1,250.00 per ton").value == 1250.0


def test_currency():
    assert parse_currency("USD 12/kg").value == "USD"
    assert parse_currency("price 8€/kg").value == "EUR"
    assert parse_currency("RMB 90/kg").value == "CNY"  # alias


def test_incoterm():
    assert parse_incoterm("delivery CIF Shanghai").value == "CIF"
    assert parse_incoterm("we quote FCA").value == "FCA"
    assert parse_incoterm("no basis here").value is None


def test_moq():
    assert parse_moq("MOQ 25 kg").value == "25 kg"
    assert parse_moq("Minimum order quantity: 1 ton").value.lower() == "1 ton"


def test_documents():
    coa, tds = parse_documents("We can provide CoA and technical data sheet.")
    assert coa.value is True
    assert tds.value is True
    coa2, tds2 = parse_documents("No documents mentioned")
    assert coa2.value is False and tds2.value is False


def test_lead_time():
    assert parse_lead_time("Lead time: 15 days").value == "15 days"
    assert parse_lead_time("delivery within 2 weeks").value == "2 weeks"


def test_payment_terms():
    assert parse_payment_terms("Payment: T/T in advance").value == "T/T"
    assert "30" in parse_payment_terms("30% deposit, balance before shipment").value


def test_grade():
    assert parse_grade("USP grade material").value.lower().startswith("usp")
    assert "99.5" in parse_grade("purity 99.5% min").value
