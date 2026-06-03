"""Эндпоинт верификации вещества по CAS."""

from fastapi import APIRouter, Query

from app.connectors.pubchem import PubChemConnector

router = APIRouter(prefix="/substances", tags=["substances"])


@router.get("/verify")
def verify_substance(cas: str = Query(..., description="CAS-номер, напр. 50-78-2")) -> dict:
    """Проверяет вещество: контрольная сумма CAS + данные PubChem.

    Echemi на этом этапе не запрашивается (заглушка в UI).
    """
    info = PubChemConnector().verify_cas(cas)
    return info.as_dict()
