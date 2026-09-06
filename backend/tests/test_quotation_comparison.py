"""Контракт безопасного сравнения целевых и исторических цен."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.schemas.quotation import QuotationCreate
from app.schemas.rfq import RFQCreate
from app.services.quotation_service import build_summary, create_quotation
from app.services.rfq_service import create_rfq


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _rfq(
    db,
    name: str,
    *,
    target_price: float | None = None,
    target_unit: str | None = None,
    target_incoterm: str | None = None,
):
    return create_rfq(
        db,
        RFQCreate(
            cas="64-17-5",
            name=name,
            incoterms=["EXW", "DAP"],
            volume="500 kg",
            target_price=target_price,
            currency="USD",
            target_price_unit=target_unit,
            target_price_incoterm=target_incoterm,
        ),
        verify=False,
    )


def _quote(
    db,
    rfq_id: int,
    price: float,
    *,
    currency: str = "USD",
    unit: str | None = "kg",
    incoterm: str = "EXW",
    grade: str | None = "Industrial grade",
    quantity: str | None = "500 kg",
):
    return create_quotation(
        db,
        QuotationCreate(
            rfq_id=rfq_id,
            price=price,
            currency=currency,
            price_unit=unit,
            incoterm=incoterm,
            grade=grade,
            quoted_quantity=quantity,
        ),
    )


def test_exact_basis_returns_absolute_percent_and_history_range(db):
    for index, price in enumerate((8, 10, 12), start=1):
        historical_rfq = _rfq(db, f"Historical ethanol {index}")
        _quote(db, historical_rfq.id, price)

    current = _rfq(
        db,
        "Current ethanol",
        target_price=10,
        target_unit="kg",
        target_incoterm="EXW",
    )
    quotation = _quote(db, current.id, 11, quantity="500KG")

    row = build_summary(db, current.id)[0]
    assert row.quotation_id == quotation.id
    assert row.target_comparison_status == "comparable"
    assert row.target_price_deviation == 1
    assert row.target_price_deviation_percent == 10
    assert row.historical_comparison_status == "comparable"
    assert row.historical_price == 10
    assert row.historical_min_price == 8
    assert row.historical_max_price == 12
    assert row.historical_sample_size == 3
    assert row.historical_price_deviation == 1
    assert row.historical_price_deviation_percent == 10


@pytest.mark.parametrize(
    ("unit", "incoterm", "quantity"),
    [
        ("MT", "EXW", "500 kg"),
        ("kg", "DAP", "500 kg"),
        ("kg", "EXW", "1 t"),
        (None, "EXW", "500 kg"),
    ],
)
def test_incompatible_basis_or_volume_never_produces_deviation(
    db, unit, incoterm, quantity
):
    current = _rfq(
        db,
        f"Mismatch {unit} {incoterm} {quantity}",
        target_price=10,
        target_unit="kg",
        target_incoterm="EXW",
    )
    _quote(db, current.id, 9, unit=unit, incoterm=incoterm, quantity=quantity)

    row = build_summary(db, current.id)[0]
    assert row.target_comparison_status == "not_comparable"
    assert row.target_price_deviation is None
    assert row.target_price_deviation_percent is None
    assert row.target_comparison_reason


def test_history_filters_grade_and_selected_period(db):
    old_rfq = _rfq(db, "Old ethanol")
    old = _quote(db, old_rfq.id, 5)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=500)
    db.commit()

    other_grade_rfq = _rfq(db, "Other grade ethanol")
    _quote(db, other_grade_rfq.id, 7, grade="USP")

    current = _rfq(db, "Period ethanol")
    _quote(db, current.id, 9)

    recent = build_summary(db, current.id, history_days=365)[0]
    assert recent.historical_comparison_status == "not_comparable"
    assert recent.historical_sample_size == 0

    extended = build_summary(db, current.id, history_days=730)[0]
    assert extended.historical_comparison_status == "comparable"
    assert extended.historical_price == 5
    assert extended.historical_sample_size == 1


def test_zero_target_price_is_not_divided(db):
    current = _rfq(
        db,
        "Zero target ethanol",
        target_price=0,
        target_unit="kg",
        target_incoterm="EXW",
    )
    _quote(db, current.id, 9)

    row = build_summary(db, current.id)[0]
    assert row.target_comparison_status == "not_comparable"
    assert row.target_price_deviation is None
    assert row.target_price_deviation_percent is None
    assert "больше нуля" in row.target_comparison_reason
