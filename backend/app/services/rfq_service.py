"""Прикладной сервис RFQ (L2): создание запроса с верификацией и генерацией.

Оркеструет шаги функций 1–2 ТЗ: приём входных данных → верификация вещества
по CAS → генерация стандартизированного RFQ → сохранение со статусом.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.connectors.pubchem import PubChemConnector
from app.models.enums import RFQStatus
from app.models.rfq import RFQ
from app.schemas.rfq import RFQCreate
from app.services.rfq_builder import RFQInput, build_rfq


def create_rfq(db: Session, data: RFQCreate, *, verify: bool = True) -> RFQ:
    """Создаёт и сохраняет RFQ.

    Валидирует базисы (через build_rfq), при verify=True проверяет вещество
    по CAS (PubChem) и проставляет статус VERIFIED/DRAFT.
    """
    # Валидация базисов выполняется здесь (бросит UnsupportedIncotermError).
    build_rfq(
        RFQInput(
            cas=data.cas,
            name=data.name,
            incoterms=data.incoterms,
            purity=data.purity,
            application=data.application,
            volume=data.volume,
            target_price=data.target_price,
            currency=data.currency,
        )
    )

    verification = None
    verified = False
    status = RFQStatus.DRAFT
    if verify:
        info = PubChemConnector().verify_cas(data.cas)
        verification = info.as_dict()
        verified = info.found
        status = RFQStatus.VERIFIED if info.found else RFQStatus.DRAFT

    rfq = RFQ(
        cas=data.cas,
        name=data.name,
        purity=data.purity,
        application=data.application,
        volume=data.volume,
        target_price=data.target_price,
        currency=data.currency,
        incoterms=[i.strip().upper() for i in data.incoterms],
        channels=data.channels or [],
        status=status,
        verified=verified,
        verification=verification,
    )
    db.add(rfq)
    db.commit()
    db.refresh(rfq)
    return rfq


def render_rfq_text(rfq: RFQ) -> tuple[str, str]:
    """Генерирует (subject, body) RFQ из сохранённой записи."""
    result = build_rfq(
        RFQInput(
            cas=rfq.cas,
            name=rfq.name,
            incoterms=list(rfq.incoterms or []),
            purity=rfq.purity,
            application=rfq.application,
            volume=rfq.volume,
            target_price=float(rfq.target_price) if rfq.target_price else None,
            currency=rfq.currency or "USD",
        )
    )
    return result["subject"], result["body"]
