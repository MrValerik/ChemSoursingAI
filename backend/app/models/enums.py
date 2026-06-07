"""Перечисления домена закупок."""

import enum


class RFQStatus(str, enum.Enum):
    """Статусы жизненного цикла запроса (раздел 10 плана: ведение статусов)."""

    DRAFT = "draft"              # создан, не верифицирован
    VERIFIED = "verified"        # вещество подтверждено по CAS
    SENT = "sent"                # RFQ разосланы
    COLLECTING = "collecting"    # идёт сбор ответов
    PARSED = "parsed"            # ответы извлечены
    SUMMARIZED = "summarized"    # сформирована сводная таблица
    ESCALATED = "escalated"      # передан специалисту
    CLOSED = "closed"


class Incoterm(str, enum.Enum):
    """Базисы поставки (целевые для MVP)."""

    CIP = "CIP"   # CIP Moscow
    FCA = "FCA"   # FCA Shanghai
    EXW = "EXW"


class SupplierType(str, enum.Enum):
    MANUFACTURER = "manufacturer"
    DISTRIBUTOR = "distributor"


class Channel(str, enum.Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class CommDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class EscalationReason(str, enum.Enum):
    GRADE = "grade"             # вопрос по грейду
    LOGISTICS = "logistics"     # логистика/опасный груз
    SHORTAGE = "shortage"       # дефицит
    CUSTOM_SYNTHESIS = "custom_synthesis"
    LOW_CONFIDENCE = "low_confidence"
    OTHER = "other"


class DispatchStatus(str, enum.Enum):
    """Статус доставки RFQ получателю (раздел 10 UI/UX-плана)."""

    QUEUED = "queued"          # в очереди
    SENT = "sent"              # отправлено
    DELIVERED = "delivered"    # доставлено
    READ = "read"              # прочитано
    ERROR = "error"            # ошибка канала


class UserRole(str, enum.Enum):
    """Роли пользователей (раздел 4 UI/UX-плана: RBAC)."""

    BUYER = "buyer"        # закупщик: ведёт свои запросы
    HEAD = "head"          # руководитель отдела: видит все, переназначает
    ADMIN = "admin"        # администратор: пользователи, роли, каналы
    AUDITOR = "auditor"    # аудитор: только чтение всех данных


class EscalationStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
