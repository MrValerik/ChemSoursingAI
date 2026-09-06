"""Прикладной сервис котировок (L2): создание с контролем полноты,
сводная таблица по RFQ, авто-эскалация (функции 6, 7, 9 ТЗ)."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.communication import Communication
from app.models.escalation import Escalation
from app.models.integration import CommunicationTestRun
from app.models.intermediary import Intermediary
from app.models.enums import CommDirection, EscalationStatus, RFQStatus, SupplierType
from app.models.purchase_decision import PurchaseDecision, PurchaseHistoryEntry
from app.models.quotation import Quotation, QuotationFieldAudit
from app.models.rfq import RFQ
from app.models.user import User
from app.schemas.quotation import (
    PurchaseHistoryRead,
    QuotationCreate,
    QuotationUpdate,
    SummaryRow,
)
from app.services.completeness import (
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    evaluate_completeness,
)
from app.services.communication_links import communication_linked_to_rfq
from app.services.escalation_rules import detect_escalation
from app.services.field_provenance import quotation_sources


DETAILED_EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("decision", "Выбор поставщика"),
    ("supplier", "Поставщик"),
    ("manufacturer", "Производитель"),
    ("country", "Страна"),
    ("packaging", "Фасовка"),
    ("grade", "Грейд"),
    ("hazmat", "HAZMAT"),
    ("price", "Цена"),
    ("price_unit", "Единица цены"),
    ("quantity", "Предложенный объём"),
    ("moq", "MOQ"),
    ("purchase", "Стоимость закупки"),
    ("delivery", "Доставка"),
    ("duty", "Пошлина"),
    ("vat", "НДС"),
    ("landed", "Итого до склада"),
    ("incoterm", "Incoterm"),
    ("payment", "Условия оплаты"),
    ("lead_time", "Срок поставки"),
    ("documents", "Документы"),
    ("status", "Полнота"),
)


def create_quotation(
    db: Session,
    data: QuotationCreate,
    *,
    source_communication_id: int | None = None,
) -> Quotation:
    """Сохраняет котировку, вычисляет полноту и при необходимости заводит
    эскалацию специалисту."""
    quote_dict = {
        "price": data.price,
        "currency": data.currency,
        "incoterm": data.incoterm,
        "moq": data.moq,
        "grade": data.grade,
        "payment_terms": data.payment_terms,
        "lead_time": data.lead_time,
        "has_coa": data.has_coa,
        "has_tds": data.has_tds,
    }
    completeness = evaluate_completeness(quote_dict, data.field_confidence)

    quotation = Quotation(
        rfq_id=data.rfq_id,
        manager_id=data.manager_id,
        source_communication_id=source_communication_id,
        price=data.price,
        currency=data.currency,
        incoterm=data.incoterm,
        moq=data.moq,
        grade=data.grade,
        payment_terms=data.payment_terms,
        lead_time=data.lead_time,
        manufacturer=data.manufacturer,
        origin_country=data.origin_country,
        packaging=data.packaging,
        price_unit=data.price_unit,
        quoted_quantity=data.quoted_quantity,
        total_price=data.total_price,
        delivery_cost=data.delivery_cost,
        duty_cost=data.duty_cost,
        vat_cost=data.vat_cost,
        landed_cost=data.landed_cost,
        cost_currency=data.cost_currency,
        is_hazmat=data.is_hazmat,
        has_coa=data.has_coa,
        has_tds=data.has_tds,
        is_complete=completeness.is_complete,
        field_confidence=data.field_confidence,
        field_provenance=quotation_sources(
            {
                field: getattr(data, field)
                for field in (
                    "price",
                    "currency",
                    "incoterm",
                    "moq",
                    "grade",
                    "payment_terms",
                    "lead_time",
                    "manufacturer",
                    "origin_country",
                    "packaging",
                    "price_unit",
                    "quoted_quantity",
                    "total_price",
                    "delivery_cost",
                    "duty_cost",
                    "vat_cost",
                    "landed_cost",
                    "cost_currency",
                    "is_hazmat",
                    "has_coa",
                    "has_tds",
                )
            },
            "supplier_reply" if source_communication_id is not None else "manual",
        ),
    )
    db.add(quotation)

    # Авто-эскалация нестандартного кейса.
    reason = detect_escalation(quote_dict, completeness, free_text=data.source_text)
    if reason is not None:
        db.add(
            Escalation(
                rfq_id=data.rfq_id,
                reason=reason,
                status=EscalationStatus.OPEN,
                note=f"Auto-escalated: {reason.value}",
            )
        )

    db.commit()
    db.refresh(quotation)
    return quotation


def update_quotation(
    db: Session,
    *,
    quotation: Quotation,
    data: QuotationUpdate,
    actor: User | None = None,
) -> Quotation:
    """Сохраняет ручные правки и заново вычисляет полноту котировки."""
    requested_fields = data.model_fields_set
    if not requested_fields:
        raise ValueError("Не передано ни одного поля для изменения")

    changed_fields = {
        field_name
        for field_name in requested_fields
        if getattr(quotation, field_name) != getattr(data, field_name)
    }
    if not changed_fields:
        return quotation

    provenance = dict(quotation.field_provenance or {})
    for field_name in changed_fields:
        old_value = getattr(quotation, field_name)
        new_value = getattr(data, field_name)
        db.add(
            QuotationFieldAudit(
                quotation_id=quotation.id,
                actor_id=actor.id if actor else None,
                field_name=field_name,
                old_value=_audit_value(old_value),
                new_value=_audit_value(new_value),
                old_source=provenance.get(field_name),
                new_source="human",
            )
        )
        setattr(quotation, field_name, getattr(data, field_name))
        if new_value is None or (isinstance(new_value, str) and not new_value.strip()):
            provenance.pop(field_name, None)
        else:
            provenance[field_name] = "human"
    quotation.field_provenance = provenance or None

    confidence = dict(quotation.field_confidence or {})
    confidence_fields = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    for field_name in changed_fields & confidence_fields:
        value = getattr(quotation, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            confidence.pop(field_name, None)
        else:
            # Значение ввёл сотрудник, поэтому оно больше не зависит от
            # вероятности автоматического извлечения из письма.
            confidence[field_name] = 1.0

    quotation.field_confidence = confidence or None
    quote_dict = {
        field_name: getattr(quotation, field_name)
        for field_name in REQUIRED_FIELDS
    }
    quote_dict["has_coa"] = quotation.has_coa
    quote_dict["has_tds"] = quotation.has_tds
    quotation.is_complete = evaluate_completeness(
        quote_dict,
        quotation.field_confidence,
    ).is_complete

    db.commit()
    db.refresh(quotation)
    return quotation


def _audit_value(value: object) -> object:
    if hasattr(value, "as_integer_ratio") and not isinstance(value, (bool, int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return value


def _basis_key(
    currency: str | None,
    price_unit: str | None,
    incoterm: str | None,
) -> tuple[str, str, str] | None:
    """Возвращает точный базис без экономических догадок и конвертаций."""
    values = (
        (currency or "").strip().upper(),
        (price_unit or "").strip().casefold(),
        (incoterm or "").strip().upper(),
    )
    return values if all(values) else None


def _deviation_percent(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference <= 0:
        return None
    return round((value - reference) / reference * 100, 2)


def _deviation(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None:
        return None
    return round(value - reference, 4)


_QUANTITY_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(mg|g|kg|t|ml|l)\s*$",
    re.IGNORECASE,
)


def _quantity_key(value: str | None) -> tuple[str, str] | None:
    match = _QUANTITY_RE.fullmatch(value or "")
    if match is None:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ".")).normalize()
    except InvalidOperation:
        return None
    return format(amount, "f"), match.group(2).casefold()


def _text_key(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _target_comparison(rfq: RFQ, quotation: Quotation) -> dict[str, object]:
    target_price = float(rfq.target_price) if rfq.target_price is not None else None
    common = {
        "target_price": target_price,
        "target_currency": rfq.currency,
        "target_price_unit": rfq.target_price_unit,
        "target_price_incoterm": rfq.target_price_incoterm,
        "target_price_deviation_percent": None,
        "target_price_deviation": None,
    }
    if target_price is None:
        return {
            **common,
            "target_comparison_status": "no_target",
            "target_comparison_reason": "Ориентир цены не задан.",
        }
    target_basis = _basis_key(
        rfq.currency,
        rfq.target_price_unit,
        rfq.target_price_incoterm,
    )
    quote_basis = _basis_key(
        quotation.currency,
        quotation.price_unit,
        quotation.incoterm,
    )
    if target_basis is None:
        return {
            **common,
            "target_comparison_status": "not_comparable",
            "target_comparison_reason": (
                "У ориентира не указаны валюта, единица цены и Incoterm."
            ),
        }
    if quote_basis is None or quotation.price is None:
        return {
            **common,
            "target_comparison_status": "not_comparable",
            "target_comparison_reason": (
                "В котировке нет цены или полного базиса: валюта, единица и Incoterm."
            ),
        }
    if quote_basis != target_basis:
        return {
            **common,
            "target_comparison_status": "not_comparable",
            "target_comparison_reason": (
                "Базис котировки не совпадает с ориентиром; автоматическая "
                "конвертация валют, единиц и логистики отключена."
            ),
        }
    target_quantity = _quantity_key(rfq.volume)
    quote_quantity = _quantity_key(quotation.quoted_quantity)
    if rfq.volume and (target_quantity is None or quote_quantity != target_quantity):
        return {
            **common,
            "target_comparison_status": "not_comparable",
            "target_comparison_reason": (
                "Объём котировки не подтверждён или не совпадает с объёмом RFQ."
            ),
        }
    deviation = _deviation_percent(float(quotation.price), target_price)
    if deviation is None:
        return {
            **common,
            "target_comparison_status": "not_comparable",
            "target_comparison_reason": "Ориентир должен быть больше нуля.",
        }
    return {
        **common,
        "target_comparison_status": "comparable",
        "target_comparison_reason": (
            "Валюта, единица цены и Incoterm совпадают точно."
        ),
        "target_price_deviation_percent": deviation,
        "target_price_deviation": _deviation(float(quotation.price), target_price),
    }


def _related_quotation_history(
    db: Session,
    rfq: RFQ,
    history_days: int,
    history_owner_id: int | None,
) -> list[Quotation]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    candidates = list(
        db.scalars(
            select(Quotation)
            .join(RFQ, RFQ.id == Quotation.rfq_id)
            .where(
                Quotation.rfq_id != rfq.id,
                Quotation.created_at >= cutoff,
            )
            .order_by(Quotation.created_at.desc(), Quotation.id.desc())
        ).all()
    )
    if history_owner_id is not None:
        candidates = [
            item
            for item in candidates
            if item.rfq.owner_id in (None, history_owner_id)
        ]
    if rfq.substance_id is not None:
        return [item for item in candidates if item.rfq.substance_id == rfq.substance_id]
    cas = (rfq.cas or "").strip().casefold()
    if not cas:
        return []
    return [
        item
        for item in candidates
        if (item.rfq.cas or "").strip().casefold() == cas
    ]


def _historical_comparison(
    quotation: Quotation,
    history: list[Quotation],
    history_days: int,
) -> dict[str, object]:
    empty = {
        "historical_price": None,
        "historical_currency": None,
        "historical_price_unit": None,
        "historical_incoterm": None,
        "historical_purchase_history_id": None,
        "historical_quotation_ids": [],
        "historical_min_price": None,
        "historical_max_price": None,
        "historical_sample_size": 0,
        "historical_period_days": history_days,
        "historical_price_deviation_percent": None,
        "historical_price_deviation": None,
    }
    if not history:
        return {
            **empty,
            "historical_comparison_status": "not_found",
            "historical_comparison_reason": (
                "Для этого вещества нет сохранённой истории выбора закупки."
            ),
        }
    quote_basis = _basis_key(
        quotation.currency,
        quotation.price_unit,
        quotation.incoterm,
    )
    if quote_basis is None or quotation.price is None:
        return {
            **empty,
            "historical_comparison_status": "not_comparable",
            "historical_comparison_reason": (
                "В котировке нет цены или полного базиса для сравнения с историей."
            ),
        }
    current_quantity = _quantity_key(quotation.quoted_quantity)
    current_grade = _text_key(quotation.grade)
    comparable: list[Quotation] = []
    for item in history:
        if item.price is None or float(item.price) <= 0:
            continue
        if _basis_key(item.currency, item.price_unit, item.incoterm) != quote_basis:
            continue
        if _text_key(item.grade) != current_grade:
            continue
        if _quantity_key(item.quoted_quantity) != current_quantity:
            continue
        comparable.append(item)
    if comparable:
        prices = [float(item.price) for item in comparable if item.price is not None]
        reference = float(median(prices))
        return {
            "historical_price": reference,
            "historical_currency": quotation.currency,
            "historical_price_unit": quotation.price_unit,
            "historical_incoterm": quotation.incoterm,
            "historical_purchase_history_id": None,
            "historical_quotation_ids": [item.id for item in comparable],
            "historical_min_price": min(prices),
            "historical_max_price": max(prices),
            "historical_sample_size": len(prices),
            "historical_period_days": history_days,
            "historical_comparison_status": "comparable",
            "historical_comparison_reason": (
                "Медиана прошлых котировок того же вещества, грейда, объёма "
                "и точного ценового базиса за выбранный период."
            ),
            "historical_price_deviation_percent": _deviation_percent(
                float(quotation.price), reference
            ),
            "historical_price_deviation": _deviation(
                float(quotation.price), reference
            ),
        }
    return {
        **empty,
        "historical_comparison_status": "not_comparable",
        "historical_comparison_reason": (
            "История есть, но нет цены того же грейда и объёма с той же "
            "валютой, единицей и Incoterm за выбранный период."
        ),
    }


def build_summary(
    db: Session,
    rfq_id: int,
    *,
    history_days: int = 365,
    history_owner_id: int | None = None,
) -> list[SummaryRow]:
    """Одна строка на поставщика с безопасным сравнением цен по базису."""
    rfq = db.get(RFQ, rfq_id)
    if rfq is None:
        return []
    quotation_history = _related_quotation_history(
        db,
        rfq,
        history_days,
        history_owner_id,
    )
    stmt = (
        select(Quotation)
        .where(Quotation.rfq_id == rfq_id)
        .order_by(Quotation.created_at, Quotation.id)
    )
    test_run_by_quotation_id = {
        run.quotation_id: run.id
        for run in db.scalars(
            select(CommunicationTestRun).where(
                CommunicationTestRun.rfq_id == rfq_id,
                CommunicationTestRun.quotation_id.is_not(None),
            )
        ).all()
        if run.quotation_id is not None
    }
    latest_channel_by_manager: dict[int, str] = {}
    for communication in db.scalars(
        select(Communication)
        .where(
            communication_linked_to_rfq(rfq_id),
            Communication.manager_id.is_not(None),
        )
        .order_by(Communication.created_at, Communication.id)
    ).all():
        if communication.manager_id is not None:
            latest_channel_by_manager[communication.manager_id] = (
                communication.channel.value
            )
    quotations = db.scalars(stmt).all()
    grouped: dict[tuple[str, str | int], list[Quotation]] = {}
    for quotation in quotations:
        manager = quotation.manager
        supplier_row = manager.supplier if manager else None
        if supplier_row is not None:
            company_key = (supplier_row.company_key or "").strip().casefold()
            group_key = (
                "supplier",
                company_key or f"supplier:{supplier_row.id}",
            )
        elif quotation.id in test_run_by_quotation_id:
            # Каждый тестовый прогон — отдельный синтетический поставщик. У него
            # нет реестрового supplier_id/company_key, поэтому одинаковая
            # подпись «Тестовый поставщик» не означает одну компанию.
            group_key = ("test_supplier", quotation.id)
        elif quotation.manager_id is not None:
            group_key = ("manager", quotation.manager_id)
        else:
            # У ручной котировки без контрагента нет безопасного ключа для
            # объединения: две безымянные записи могут относиться к разным лицам.
            group_key = ("quotation", quotation.id)
        grouped.setdefault(group_key, []).append(quotation)

    rows: list[SummaryRow] = []
    for supplier_quotations in grouped.values():
        q = supplier_quotations[-1]
        manager = q.manager
        supplier_row = manager.supplier if manager else None
        supplier = supplier_row.company if supplier_row else None
        supplier_types = {
            item.manager.supplier.type
            for item in supplier_quotations
            if item.manager is not None
            and item.manager.supplier is not None
            and item.manager.supplier.type is not None
        }
        supplier_is_manufacturer = (
            next(iter(supplier_types)) == SupplierType.MANUFACTURER
            if len(supplier_types) == 1
            else None
        )
        rows.append(
            SummaryRow(
                quotation_id=q.id,
                quotation_ids=[item.id for item in supplier_quotations],
                quotation_count=len(supplier_quotations),
                supplier_id=manager.supplier_id if manager else None,
                manager_id=q.manager_id,
                test_run_id=test_run_by_quotation_id.get(q.id),
                conversation_channel=(
                    latest_channel_by_manager.get(q.manager_id)
                    if q.manager_id is not None
                    else None
                ),
                supplier=(
                    supplier
                    or (
                        "Тестовый поставщик"
                        if q.id in test_run_by_quotation_id
                        else None
                    )
                ),
                supplier_is_manufacturer=supplier_is_manufacturer,
                manager=manager.full_name if manager else None,
                price=float(q.price) if q.price is not None else None,
                currency=q.currency,
                price_provenance=(
                    "test"
                    if q.id in test_run_by_quotation_id
                    else "manual"
                    if (q.field_provenance or {}).get("price") == "human"
                    else "supplier_reply"
                    if (
                        (q.field_provenance or {}).get("price") == "supplier_reply"
                        or q.source_communication_id is not None
                    )
                    else "manual"
                ),
                price_source_communication_id=q.source_communication_id,
                incoterm=q.incoterm,
                moq=q.moq,
                grade=q.grade,
                payment_terms=q.payment_terms,
                lead_time=q.lead_time,
                manufacturer=q.manufacturer,
                origin_country=q.origin_country or (
                    supplier_row.country if supplier_row else None
                ),
                packaging=q.packaging,
                price_unit=q.price_unit,
                quoted_quantity=q.quoted_quantity,
                total_price=(
                    float(q.total_price) if q.total_price is not None else None
                ),
                delivery_cost=(
                    float(q.delivery_cost) if q.delivery_cost is not None else None
                ),
                duty_cost=(
                    float(q.duty_cost) if q.duty_cost is not None else None
                ),
                vat_cost=float(q.vat_cost) if q.vat_cost is not None else None,
                landed_cost=(
                    float(q.landed_cost) if q.landed_cost is not None else None
                ),
                cost_currency=q.cost_currency or q.currency,
                is_hazmat=q.is_hazmat,
                has_coa=q.has_coa,
                has_tds=q.has_tds,
                is_complete=q.is_complete,
                field_confidence=q.field_confidence,
                field_provenance=q.field_provenance,
                **_target_comparison(rfq, q),
                **_historical_comparison(q, quotation_history, history_days),
                created_at=q.created_at,
            )
        )

    # Разные валюты и базисы нельзя ранжировать по голому числу цены. После
    # полноты порядок детерминированный и нейтральный — по поставщику/менеджеру.
    rows.sort(
        key=lambda r: (
            not r.is_complete,
            (r.supplier or r.manager or "").casefold(),
            r.quotation_id,
        )
    )

    # Перевод статуса RFQ в SUMMARIZED, если есть хоть одна котировка.
    if rows:
        if rfq.status in (RFQStatus.SENT, RFQStatus.COLLECTING, RFQStatus.PARSED):
            rfq.status = RFQStatus.SUMMARIZED
            db.commit()
    return rows


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    # Даже корректно экранированная CSV-ячейка остаётся формулой для Excel.
    return f"'{text}" if re.match(r"^\s*[=+\-@]", text) else text


def _detailed_export_value(
    key: str,
    row: SummaryRow,
    selected_quotation_id: int | None,
) -> object:
    if key == "decision":
        return "выбрано" if selected_quotation_id in row.quotation_ids else ""
    if key == "supplier":
        return row.supplier or row.manager
    if key == "manufacturer":
        if row.supplier_is_manufacturer is None:
            return "не определено"
        return "да" if row.supplier_is_manufacturer else "нет"
    if key == "country":
        return row.origin_country
    if key == "packaging":
        return row.packaging
    if key == "grade":
        return row.grade
    if key == "hazmat":
        return "" if row.is_hazmat is None else "да" if row.is_hazmat else "нет"
    if key == "price":
        return row.price
    if key == "price_unit":
        return row.price_unit
    if key == "quantity":
        return row.quoted_quantity
    if key == "moq":
        return row.moq
    if key == "purchase":
        return row.total_price
    if key == "delivery":
        return row.delivery_cost
    if key == "duty":
        return row.duty_cost
    if key == "vat":
        return row.vat_cost
    if key == "landed":
        return row.landed_cost
    if key == "incoterm":
        return row.incoterm
    if key == "payment":
        return row.payment_terms
    if key == "lead_time":
        return row.lead_time
    if key == "documents":
        return " · ".join(
            item
            for item in (
                "CoA" if row.has_coa else "",
                "TDS" if row.has_tds else "",
            )
            if item
        )
    if key == "status":
        return "полная" if row.is_complete else "неполная"
    raise ValueError(f"Неизвестный столбец экспорта: {key}")


def _write_csv(headers: list[str], rows: list[list[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter=";",
        quoting=csv.QUOTE_ALL,
        lineterminator="\r\n",
    )
    writer.writerow(headers)
    writer.writerows([[_csv_safe(value) for value in row] for row in rows])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def build_summary_csv(
    db: Session,
    rfq: RFQ,
    *,
    mode: str,
    history_days: int = 365,
    history_owner_id: int | None = None,
    columns: list[str] | None = None,
) -> tuple[bytes, str]:
    """Единый серверный контракт подробного и компактного CSV."""
    decision = db.scalar(
        select(PurchaseDecision).where(PurchaseDecision.rfq_id == rfq.id)
    )
    if mode == "compact":
        if decision is None:
            raise ValueError(
                "Компактный экспорт доступен после ручного сохранения выбранного предложения."
            )
        quotation = db.get(Quotation, decision.quotation_id)
        if quotation is None or quotation.rfq_id != rfq.id:
            raise ValueError("Сохранённое предложение больше не найдено.")
        manager = quotation.manager
        supplier = manager.supplier if manager else None
        headers = [
            "Вещество",
            "CAS",
            "Выбранная цена",
            "Валюта",
            "Единица цены",
            "Incoterm",
            "Поставщик",
            "Дата решения",
        ]
        row = [
            rfq.name,
            rfq.cas,
            float(quotation.price) if quotation.price is not None else None,
            quotation.currency,
            quotation.price_unit,
            quotation.incoterm,
            (
                supplier.company
                if supplier
                else manager.full_name
                if manager
                else None
            ),
            decision.updated_at.isoformat(),
        ]
        return _write_csv(headers, [row]), f"rfq-{rfq.id}-selected.csv"

    if mode != "detailed":
        raise ValueError("Режим экспорта: detailed или compact.")
    allowed = {key for key, _ in DETAILED_EXPORT_COLUMNS}
    requested = columns or [key for key, _ in DETAILED_EXPORT_COLUMNS]
    unknown = sorted(set(requested) - allowed)
    if unknown:
        raise ValueError(f"Неизвестные столбцы экспорта: {', '.join(unknown)}")
    selected_columns = [
        (key, label)
        for key, label in DETAILED_EXPORT_COLUMNS
        if key in set(requested)
    ]
    if not selected_columns:
        raise ValueError("Выберите хотя бы один столбец подробного экспорта.")
    summary = build_summary(
        db,
        rfq.id,
        history_days=history_days,
        history_owner_id=history_owner_id,
    )
    risks = list(
        db.scalars(
            select(Escalation).where(
                Escalation.rfq_id == rfq.id,
                Escalation.status == EscalationStatus.OPEN,
            )
        ).all()
    )
    risk_text = ", ".join(sorted({item.reason.value for item in risks}))
    headers = ["Вещество", "CAS"] + [label for _, label in selected_columns] + [
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
    rows = []
    for row in summary:
        rows.append(
            [rfq.name, rfq.cas]
            + [
                _detailed_export_value(
                    key,
                    row,
                    decision.quotation_id if decision else None,
                )
                for key, _ in selected_columns
            ]
            + [
                row.currency,
                risk_text,
                row.price_provenance,
                row.price_source_communication_id,
                row.target_comparison_status,
                row.target_price_deviation,
                row.target_price_deviation_percent,
                row.historical_price,
                row.historical_min_price,
                row.historical_max_price,
                row.historical_sample_size,
                row.historical_period_days,
                row.target_comparison_reason,
            ]
        )
    return _write_csv(headers, rows), f"rfq-{rfq.id}-summary.csv"


def save_purchase_decision(
    db: Session,
    *,
    rfq: RFQ,
    quotation_id: int,
    note: str | None,
    actor: User,
) -> PurchaseDecision:
    """Сохраняет выбор человека без отправки заказа или внешнего действия."""
    quotation = db.get(Quotation, quotation_id)
    if quotation is None or quotation.rfq_id != rfq.id:
        raise ValueError("Предложение не относится к этому запросу")

    decision = db.scalar(
        select(PurchaseDecision).where(PurchaseDecision.rfq_id == rfq.id)
    )
    if decision is None:
        decision = PurchaseDecision(rfq_id=rfq.id, quotation_id=quotation.id)
        db.add(decision)
    decision.quotation_id = quotation.id
    decision.selected_by_id = actor.id
    decision.note = note
    decision.communication_mode = "manual_selected_supplier"
    supplier = quotation.manager.supplier if quotation.manager else None
    intermediary = _purchase_intermediary(db, supplier)
    cancelled_draft_ids: list[int] = []
    skipped_shared_draft_ids: list[int] = []
    skipped_unresolved_supplier_draft_ids: list[int] = []
    selected_supplier_id = supplier.id if supplier else None
    drafts = list(
        db.scalars(
            select(Communication)
            .options(
                joinedload(Communication.manager),
                joinedload(Communication.rfq_links),
            )
            .where(
                communication_linked_to_rfq(rfq.id),
                Communication.direction == CommDirection.OUTBOUND,
                Communication.status == "draft",
            )
        ).unique()
    )
    for draft in drafts:
        linked_ids = {
            *[link.rfq_id for link in draft.rfq_links],
            *([draft.rfq_id] if draft.rfq_id is not None else []),
        }
        if len(linked_ids) > 1:
            skipped_shared_draft_ids.append(draft.id)
            continue
        if selected_supplier_id is None:
            skipped_unresolved_supplier_draft_ids.append(draft.id)
            continue
        draft_supplier_id = (
            draft.manager.supplier_id if draft.manager is not None else None
        )
        if draft_supplier_id != selected_supplier_id:
            draft.status = "cancelled"
            cancelled_draft_ids.append(draft.id)
    decision.cancelled_draft_count = len(cancelled_draft_ids)
    snapshot = _purchase_snapshot(
        rfq=rfq,
        quotation=quotation,
        supplier=supplier,
        intermediary=intermediary,
    )
    snapshot.update(
        {
            "communication_mode": decision.communication_mode,
            "cancelled_draft_ids": cancelled_draft_ids,
            "skipped_shared_draft_ids": skipped_shared_draft_ids,
            "skipped_unresolved_supplier_draft_ids": (
                skipped_unresolved_supplier_draft_ids
            ),
        }
    )
    db.add(
        PurchaseHistoryEntry(
            rfq_id=rfq.id,
            quotation_id=quotation.id,
            substance_id=rfq.substance_id,
            supplier_id=supplier.id if supplier else None,
            intermediary_id=intermediary.id if intermediary else None,
            actor_id=actor.id,
            note=note,
            snapshot=snapshot,
        )
    )
    db.commit()
    db.refresh(decision)
    return decision


def _purchase_intermediary(db: Session, supplier) -> Intermediary | None:
    """Связывает итог с реестром посредников только по проверяемым признакам."""
    if supplier is None:
        return None
    from app.services.intermediaries import domain_label, normalize_domain

    source_domain = normalize_domain(supplier.source or "")
    source_label = domain_label(source_domain) if "." in source_domain else ""
    company = supplier.company.strip().casefold()
    for item in db.scalars(select(Intermediary)).all():
        item_domain = normalize_domain(item.domain)
        if source_domain and (
            source_domain == item_domain
            or source_domain.endswith(f".{item_domain}")
            or (source_label and source_label == domain_label(item_domain))
        ):
            return item
        if (
            supplier.type == SupplierType.DISTRIBUTOR
            and company == item.name.strip().casefold()
        ):
            return item
    return None


def _purchase_snapshot(*, rfq: RFQ, quotation: Quotation, supplier, intermediary) -> dict:
    """JSON-снимок сохраняет подписи и условия даже после правки реестров."""
    snapshot = {
        "rfq_name": rfq.name,
        "rfq_cas": rfq.cas,
        "rfq_volume": rfq.volume,
        "supplier_name": supplier.company if supplier else None,
        "supplier_type": supplier.type.value if supplier and supplier.type else None,
        "intermediary_name": intermediary.name if intermediary else None,
        "manufacturer": quotation.manufacturer,
        "origin_country": quotation.origin_country,
        "currency": quotation.currency,
        "cost_currency": quotation.cost_currency,
        "price_unit": quotation.price_unit,
        "quoted_quantity": quotation.quoted_quantity,
        "moq": quotation.moq,
        "incoterm": quotation.incoterm,
        "payment_terms": quotation.payment_terms,
        "lead_time": quotation.lead_time,
        "has_coa": quotation.has_coa,
        "has_tds": quotation.has_tds,
        "is_complete": quotation.is_complete,
    }
    for field in (
        "price",
        "total_price",
        "delivery_cost",
        "duty_cost",
        "vat_cost",
        "landed_cost",
    ):
        value = getattr(quotation, field)
        snapshot[field] = float(value) if value is not None else None
    return snapshot


def purchase_history_read(entry: PurchaseHistoryEntry) -> PurchaseHistoryRead:
    result = PurchaseHistoryRead.model_validate(entry, from_attributes=True)
    result.actor_name = entry.actor.full_name if entry.actor else None
    return result
