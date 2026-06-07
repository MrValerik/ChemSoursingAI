"""ORM-модели (L7). Сущности соответствуют разделу «Модель данных» ТЗ."""

from app.models.base import Base
from app.models.communication import Communication
from app.models.escalation import Escalation
from app.models.manager import Manager
from app.models.quotation import Quotation
from app.models.recipient import RfqRecipient
from app.models.rfq import RFQ
from app.models.supplier import Supplier
from app.models.template import Template
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "RFQ",
    "Supplier",
    "Template",
    "Manager",
    "Quotation",
    "RfqRecipient",
    "Communication",
    "Escalation",
]
