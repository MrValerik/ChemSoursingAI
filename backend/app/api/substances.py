"""Эндпоинт верификации вещества по CAS."""
from app.api.deps import get_current_user

from fastapi import Depends, APIRouter, Query

from app.connectors.pubchem import PubChemConnector

router = APIRouter(prefix="/substances", tags=["substances"], dependencies=[Depends(get_current_user)])


@router.get("/verify")
def verify_substance(cas: str = Query(..., description="CAS-номер, напр. 50-78-2")) -> dict:
    """Проверяет вещество: контрольная сумма CAS + данные PubChem.

    Echemi на этом этапе не запрашивается (заглушка в UI).
    """
    info = PubChemConnector().verify_cas(cas)
    return info.as_dict()
