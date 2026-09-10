from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models import User
from app.models.enums import UserRole
from app.models.echemi_search import EchemiSearch
from app.schemas.echemi_search import EchemiSearchCreate, EchemiSearchRead, EchemiSearchSummary

router = APIRouter(prefix="/echemi-searches", tags=["echemi"])


def visible(user):
    statement = select(EchemiSearch)
    if user.role == UserRole.BUYER:
        statement = statement.where(EchemiSearch.author_id == user.id)
    return statement


@router.get("", response_model=list[EchemiSearchSummary])
def history(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(visible(user).order_by(EchemiSearch.id.desc()).offset(offset).limit(limit)).all()
    return [dict(id=r.id, query=r.query, status=r.status, message=r.message,
                 created_at=r.created_at, finished_at=r.finished_at, result_count=len(r.results)) for r in rows]


@router.post("", response_model=EchemiSearchRead, status_code=201)
def create(payload: EchemiSearchCreate, db: Session = Depends(get_db),
           user: User = Depends(require_roles(UserRole.BUYER, UserRole.HEAD, UserRole.ADMIN))):
    # Limit queued browser work; a user can still intentionally repeat a completed query.
    pending = db.scalar(select(func.count()).select_from(EchemiSearch).where(
        EchemiSearch.author_id == user.id, EchemiSearch.status.in_(["queued", "running"])))
    if pending >= 5:
        raise HTTPException(429, "У вас уже пять незавершённых поисков Echemi.")
    row = EchemiSearch(author_id=user.id, query=payload.query)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{search_id}", response_model=EchemiSearchRead)
def detail(search_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.scalar(visible(user).where(EchemiSearch.id == search_id))
    if row is None:
        raise HTTPException(404, "Поиск не найден")
    return row
