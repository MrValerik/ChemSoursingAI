"""Idempotent registration of AI-qualified supplier candidates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Manager, RfqSupplierLink, SearchRun, Supplier
from app.models.enums import SupplierType
from app.services.search_trace import utc_now

# Сколько контактов одной компании имеет смысл заводить. Больше — это уже
# не отдел продаж, а разбор чужого списка рассылки.
_MAX_MANAGERS = 3


def _attach_contacts(
    db: Session,
    *,
    supplier: Supplier,
    result: dict,
    substance: str,
) -> None:
    """Заводит контакты компании, снятые со страницы при загрузке.

    Без этого поставщик попадал в «Отобранные поставщики» без канала
    связи, а галочку в таблице поставить нельзя: канал берётся из
    контактов менеджера. То есть поиск доводил до компании и на этом
    останавливался, хотя почта и телефон лежали в уже загруженной
    странице — связь нашлась у 93 карточек из 136.

    Контакты страницы площадки не берутся: там указан отдел продаж самой
    площадки, а не компании, и письмо ушло бы не туда.
    """
    if result.get("supplier_type") == "marketplace":
        return
    contacts = result.get("contacts") or {}
    emails = [str(value).strip() for value in contacts.get("emails") or []]
    whatsapp = [str(value).strip() for value in contacts.get("whatsapp") or []]
    if not emails and not whatsapp:
        return

    # Читаем из базы, а не из supplier.managers: связь после вставки в той
    # же сессии остаётся прежней, и повторный прогон заводил контакт заново.
    existing = db.scalars(
        select(Manager).where(Manager.supplier_id == supplier.id)
    ).all()
    known_emails = {(manager.email or "").casefold() for manager in existing}
    known_whatsapp = {(manager.whatsapp or "").strip() for manager in existing}
    offered = [substance] if substance else None

    added = 0
    for email in emails:
        if added >= _MAX_MANAGERS or not email or email.casefold() in known_emails:
            continue
        db.add(
            Manager(
                supplier_id=supplier.id,
                email=email[:255],
                # WhatsApp приписывается первому контакту: страница даёт
                # один номер на компанию, а не на человека.
                whatsapp=(whatsapp[0][:64] if whatsapp and added == 0 else None),
                offered_substances=offered,
            )
        )
        known_emails.add(email.casefold())
        added += 1

    if added == 0 and whatsapp and whatsapp[0] not in known_whatsapp:
        # Почты нет, но написать всё равно есть куда.
        db.add(
            Manager(
                supplier_id=supplier.id,
                whatsapp=whatsapp[0][:64],
                offered_substances=offered,
            )
        )
    db.flush()


def register_qualified_candidate(
    db: Session,
    *,
    search_run: SearchRun,
    result: dict,
) -> Supplier | None:
    """Save a verified-page result as a candidate, never as an approved supplier."""
    if search_run.rfq_id is None:
        return None

    source_url = str(result.get("url") or "").strip()
    if not source_url:
        return None

    stored_source = source_url[:255]
    supplier = db.scalar(
        select(Supplier).where(Supplier.source == stored_source).limit(1)
    )
    supplier_kind = result.get("supplier_type")
    mapped_type = (
        SupplierType(supplier_kind)
        if supplier_kind in {"manufacturer", "distributor"}
        else None
    )
    score = result.get("confidence")
    evidence_score = score if isinstance(score, int) else None
    certificates = [
        label
        for field, label in (
            ("gmp_status", "GMP"),
            ("iso_status", "ISO"),
            ("coa_status", "CoA"),
            ("tds_status", "TDS"),
        )
        if result.get(field) == "claimed"
    ]

    if supplier is None:
        company = str(
            result.get("company_name") or result.get("title") or source_url
        ).strip()
        supplier = Supplier(
            company=company[:255],
            country=(search_run.input_payload or {}).get("country"),
            type=mapped_type,
            reputation=(
                f"Автоматическая квалификация: {evidence_score}/100; "
                "требуется решение специалиста"
            )[:255]
            if evidence_score is not None
            else "Автоматическая квалификация; требуется решение специалиста",
            source=stored_source,
            certificates=certificates or None,
            qualification_status="candidate",
            evidence_score=evidence_score,
            last_checked_at=utc_now(),
        )
        db.add(supplier)
        db.flush()
    else:
        if evidence_score is not None:
            supplier.evidence_score = max(
                supplier.evidence_score or 0, evidence_score
            )
        if supplier.type is None and mapped_type is not None:
            supplier.type = mapped_type
        if certificates:
            supplier.certificates = sorted(
                set(supplier.certificates or []).union(certificates)
            )
        supplier.last_checked_at = utc_now()

    _attach_contacts(
        db,
        supplier=supplier,
        result=result,
        substance=str((search_run.input_payload or {}).get("name") or "").strip(),
    )

    link = db.scalar(
        select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == search_run.rfq_id,
            RfqSupplierLink.supplier_id == supplier.id,
        )
    )
    if link is None:
        link = RfqSupplierLink(
            rfq_id=search_run.rfq_id,
            supplier_id=supplier.id,
            search_run_id=search_run.id,
            source_url=source_url,
            status="candidate",
        )
        db.add(link)
        db.flush()
    elif link.search_run_id is None:
        link.search_run_id = search_run.id

    return supplier
