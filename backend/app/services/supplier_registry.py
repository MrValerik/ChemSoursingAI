"""Idempotent registration of AI-qualified supplier candidates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RfqSupplierLink, SearchRun, Supplier
from app.models.enums import SupplierType
from app.services.search_trace import utc_now


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
