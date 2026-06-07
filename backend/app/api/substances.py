"""Эндпоинт верификации вещества по CAS."""
from app.api.deps import get_current_user

from fastapi import Depends, APIRouter, Query

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.pubchem import PubChemConnector
from app.core.db import get_db
from app.models.quotation import Quotation
from app.models.rfq import RFQ

router = APIRouter(prefix="/substances", tags=["substances"], dependencies=[Depends(get_current_user)])


@router.get("/verify")
def verify_substance(cas: str = Query(..., description="CAS-номер, напр. 50-78-2")) -> dict:
    """Проверяет вещество: контрольная сумма CAS + данные PubChem.

    Echemi на этом этапе не запрашивается (заглушка в UI).
    """
    info = PubChemConnector().verify_cas(cas)
    return info.as_dict()


@router.get("/price-history")
def price_history(
    cas: str = Query(..., description="CAS-номер"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """История закупочных цен по веществу (раздел 8 UI/UX-плана).

    Источник — котировки прошлых запросов с тем же CAS: специалист сразу
    видит ориентир и оценивает адекватность новых предложений.
    """
    stmt = (
        select(Quotation, RFQ.id.label("rfq_id"))
        .join(RFQ, RFQ.id == Quotation.rfq_id)
        .where(RFQ.cas == cas.strip(), Quotation.price.is_not(None))
        .order_by(Quotation.created_at.desc())
        .limit(20)
    )
    rows = db.execute(stmt).all()
    return [
        {
            "rfq_id": rfq_id,
            "date": q.created_at.date().isoformat(),
            "price": float(q.price),
            "currency": q.currency,
            "incoterm": q.incoterm,
            "moq": q.moq,
        }
        for q, rfq_id in rows
    ]
