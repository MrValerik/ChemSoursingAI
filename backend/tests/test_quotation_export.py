"""Контракт двух CSV-экспортов сводной таблицы."""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Manager, PurchaseDecision, Supplier
from app.schemas.quotation import QuotationCreate
from app.schemas.rfq import RFQCreate
from app.services.quotation_service import build_summary_csv, create_quotation
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


def _read_csv(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig")), delimiter=";"))


def _data(db):
    rfq = create_rfq(
        db,
        RFQCreate(
            cas="64-17-5",
            name='Ethanol, "export"\nline',
            incoterms=["CIP"],
            volume="500 kg",
            target_price=10,
            currency="USD",
            target_price_unit="kg",
            target_price_incoterm="CIP",
        ),
        verify=False,
    )
    supplier = Supplier(company="=2+2 Formula Supplier", company_key="formula")
    db.add(supplier)
    db.flush()
    manager = Manager(
        supplier_id=supplier.id,
        full_name="Sales",
        email="sales@formula.example",
    )
    db.add(manager)
    db.flush()
    quotation = create_quotation(
        db,
        QuotationCreate(
            rfq_id=rfq.id,
            manager_id=manager.id,
            price=11,
            currency="USD",
            price_unit="kg",
            incoterm="CIP",
            quoted_quantity="500 kg",
            grade="Industrial grade",
            payment_terms='T/T, "30 days"\nconfirmed',
            has_coa=True,
        ),
    )
    return rfq, quotation


def test_detailed_export_has_stable_contract_and_excel_safe_values(db):
    rfq, _ = _data(db)
    payload, filename = build_summary_csv(db, rfq, mode="detailed")
    rows = _read_csv(payload)

    assert payload.startswith(b"\xef\xbb\xbf")
    assert filename == f"rfq-{rfq.id}-summary.csv"
    assert rows[0] == [
        "Вещество",
        "CAS",
        "Выбор поставщика",
        "Поставщик",
        "Производитель",
        "Страна",
        "Фасовка",
        "Грейд",
        "HAZMAT",
        "Цена",
        "Единица цены",
        "Предложенный объём",
        "MOQ",
        "Стоимость закупки",
        "Доставка",
        "Пошлина",
        "НДС",
        "Итого до склада",
        "Incoterm",
        "Условия оплаты",
        "Срок поставки",
        "Документы",
        "Полнота",
        "Валюта",
        "Риски",
        "Источник цены",
        "ID сообщения-источника",
        "Сравнение с ориентиром",
        "Абсолютное отклонение от ориентира",
        "Отклонение от ориентира, %",
        "Медиана истории",
        "Минимум истории",
        "Максимум истории",
        "Количество в истории",
        "Период истории, дней",
        "Причина сравнения",
    ]
    assert rows[1][0] == 'Ethanol, "export"\nline'
    assert rows[1][3] == "'=2+2 Formula Supplier"
    assert rows[1][19] == 'T/T, "30 days"\nconfirmed'
    assert all("prompt" not in header.casefold() for header in rows[0])


def test_compact_export_requires_and_uses_manual_decision(db):
    rfq, quotation = _data(db)
    with pytest.raises(ValueError, match="после ручного сохранения"):
        build_summary_csv(db, rfq, mode="compact")

    decision = PurchaseDecision(rfq_id=rfq.id, quotation_id=quotation.id)
    db.add(decision)
    db.commit()
    payload, filename = build_summary_csv(db, rfq, mode="compact")
    rows = _read_csv(payload)

    assert filename == f"rfq-{rfq.id}-selected.csv"
    assert rows[0] == [
        "Вещество",
        "CAS",
        "Выбранная цена",
        "Валюта",
        "Единица цены",
        "Incoterm",
        "Поставщик",
        "Дата решения",
    ]
    assert len(rows) == 2
    assert rows[1][2:6] == ["11.0", "USD", "kg", "CIP"]
    assert rows[1][6] == "'=2+2 Formula Supplier"


def test_detailed_export_rejects_unknown_columns(db):
    rfq, _ = _data(db)
    with pytest.raises(ValueError, match="Неизвестные столбцы"):
        build_summary_csv(db, rfq, mode="detailed", columns=["supplier", "secret"])
