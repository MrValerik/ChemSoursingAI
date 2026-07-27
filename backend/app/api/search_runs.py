"""Read-only API for user-visible supplier-search traces."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import SearchRun, User
from app.models.enums import UserRole
from app.schemas.search_trace import SearchRunListItem, SearchRunTrace

router = APIRouter(prefix="/search-runs", tags=["search-runs"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _can_see(user: User, search_run: SearchRun) -> bool:
    return user.role in _SEE_ALL_ROLES or search_run.owner_id == user.id


def _list_item(search_run: SearchRun) -> SearchRunListItem:
    item = SearchRunListItem.model_validate(search_run)
    item.owner_name = search_run.owner.full_name if search_run.owner else None
    return item


@router.get("", response_model=list[SearchRunListItem])
def list_search_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SearchRunListItem]:
    stmt = (
        select(SearchRun)
        .options(selectinload(SearchRun.owner))
        .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(SearchRun.owner_id == user.id)
    return [_list_item(run) for run in db.scalars(stmt).all()]


@router.get("/{search_run_id}", response_model=SearchRunTrace)
def get_search_run(
    search_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SearchRunTrace:
    search_run = db.scalar(
        select(SearchRun)
        .where(SearchRun.id == search_run_id)
        .options(
            selectinload(SearchRun.owner),
            selectinload(SearchRun.agent_runs),
            selectinload(SearchRun.search_attempts),
            selectinload(SearchRun.source_documents),
        )
    )
    if search_run is None or not _can_see(user, search_run):
        raise HTTPException(status_code=404, detail="Search run not found")
    item = SearchRunTrace.model_validate(search_run)
    item.owner_name = search_run.owner.full_name if search_run.owner else None
    return item
