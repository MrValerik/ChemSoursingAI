"""RFQ-scoped browser jobs. Enqueue only; browser failures are independent."""
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import RFQ
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole


def visible_rfq(user):
    statement = select(RFQ).where(RFQ.deleted_at.is_(None))
    if user.role == UserRole.BUYER:
        statement = statement.where(or_(RFQ.owner_id == user.id, RFQ.owner_id.is_(None)))
    return statement


def visible_searches(user):
    linked = visible_rfq(user).where(RFQ.id == EchemiSearch.rfq_id).exists()
    standalone = EchemiSearch.rfq_id.is_(None)
    if user.role == UserRole.BUYER:
        standalone = and_(standalone, EchemiSearch.author_id == user.id)
    return select(EchemiSearch).where(or_(standalone, linked))


def ensure_rfq_search(db: Session, rfq_id: int, actor_id: int, *, repeat: bool = False):
    # Lock the parent so parallel country jobs cannot enqueue duplicate browser work.
    rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update(key_share=True))
    if rfq is None or rfq.deleted_at is not None or rfq.identification_method == "analog":
        return None
    query = (rfq.cas or rfq.name or "").strip()
    previous = db.scalar(select(EchemiSearch).where(
        EchemiSearch.rfq_id == rfq_id, EchemiSearch.query == query[:200]
    ).order_by(EchemiSearch.id.desc()).limit(1))
    if previous and (not repeat or previous.status in {"queued", "running"}):
        return previous
    row = EchemiSearch(rfq_id=rfq_id, author_id=actor_id, query=query[:200], status="queued")
    if not query or len(query) > 200:
        row.status = "failed"
        row.message = "Для поиска Echemi укажите CAS или название длиной от 1 до 200 символов."
        row.finished_at = datetime.now(timezone.utc)
    db.add(row)
    db.flush()
    return row
