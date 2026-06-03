"""ORM-модели (L7). Сущности соответствуют разделу «Модель данных» ТЗ."""

from app.models.base import Base
from app.models.communication import Communication
from app.models.escalation import Escalation
from app.models.manager import Manager
from app.models.quotation import Quotation
from app.models.rfq import RFQ
from app.models.supplier import Supplier

__all__ = [
    "Base",
    "RFQ",
    "Supplier",
    "Manager",
    "Quotation",
    "Communication",
    "Escalation",
]
