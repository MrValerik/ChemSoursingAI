"""Дашборд по ролям (раздел 5 UI/UX-плана).

Закупщик видит «мой день» (свои запросы), руководитель/админ/аудитор —
срез отдела с нагрузкой по закупщикам и просрочками.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User
from app.models.enums import EscalationStatus, RFQStatus, UserRole
from app.models.escalation import Escalation
from app.models.rfq import RFQ

router = APIRouter(tags=["dashboard"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}
_ACTIVE_STATUSES = (
    RFQStatus.DRAFT,
    RFQStatus.VERIFIED,
    RFQStatus.SENT,
    RFQStatus.COLLECTING,
    RFQStatus.PARSED,
    RFQStatus.ESCALATED,
)
# Просрочка: запрос старше 3 дней без сводки (раздел 5: «> 3 дней без сводки»).
_OVERDUE_DAYS = 3


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    see_all = user.role in _SEE_ALL_ROLES

    rfq_filter = []
    if not see_all:
        rfq_filter.append((RFQ.owner_id == user.id) | (RFQ.owner_id.is_(None)))

    # Запросы по статусам.
    rows = db.execute(
        select(RFQ.status, func.count(RFQ.id)).where(*rfq_filter).group_by(RFQ.status)
    ).all()
    by_status = {status.value: count for status, count in rows}
    in_work = sum(by_status.get(s.value, 0) for s in _ACTIVE_STATUSES)

    # Открытые эскалации (ручной разбор).
    esc_stmt = select(func.count(Escalation.id)).where(
        Escalation.status != EscalationStatus.RESOLVED
    )
    if not see_all:
        esc_stmt = esc_stmt.join(RFQ, RFQ.id == Escalation.rfq_id).where(*rfq_filter)
    open_escalations = db.scalar(esc_stmt) or 0

    # Просрочки: активные запросы старше N дней.
    deadline = datetime.now(timezone.utc) - timedelta(days=_OVERDUE_DAYS)
    overdue_stmt = (
        select(RFQ)
        .options(joinedload(RFQ.owner))
        .where(
            RFQ.status.in_(_ACTIVE_STATUSES),
            RFQ.created_at < deadline,
            *rfq_filter,
        )
        .order_by(RFQ.created_at)
        .limit(10)
    )
    overdue = [
        {
            "id": r.id,
            "name": r.name,
            "cas": r.cas,
            "status": r.status.value,
            "owner_name": r.owner.full_name if r.owner else None,
            "age_days": (datetime.now(timezone.utc) - r.created_at).days
            if r.created_at.tzinfo
            else (datetime.now() - r.created_at).days,
        }
        for r in db.scalars(overdue_stmt).all()
    ]

    result: dict = {
        "role": user.role.value,
        "in_work": in_work,
        "attention": open_escalations + len(overdue),
        "manual_review": open_escalations,
        "by_status": by_status,
        "overdue": overdue,
    }

    # Нагрузка по закупщикам — только для «видящих всё».
    if see_all:
        workload_rows = db.execute(
            select(User.full_name, func.count(RFQ.id))
            .join(RFQ, RFQ.owner_id == User.id)
            .where(RFQ.status.in_(_ACTIVE_STATUSES))
            .group_by(User.full_name)
            .order_by(func.count(RFQ.id).desc())
        ).all()
        result["workload"] = [
            {"owner": name, "count": count} for name, count in workload_rows
        ]

    return result
