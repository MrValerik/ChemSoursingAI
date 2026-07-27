"""Read-only API for user-visible supplier-search traces."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import SearchRun, User
from app.models.enums import UserRole
from app.schemas.search_trace import (
    SearchRunListItem,
    SearchRunSummary,
    SearchRunTrace,
)

router = APIRouter(prefix="/search-runs", tags=["search-runs"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _can_see(user: User, search_run: SearchRun) -> bool:
    return user.role in _SEE_ALL_ROLES or search_run.owner_id == user.id


def _stage_output(search_run: SearchRun, slug: str) -> dict:
    for stage in reversed(search_run.agent_runs):
        if stage.agent_slug == slug and isinstance(stage.output_payload, dict):
            return stage.output_payload
    return {}


def _candidate_results(search_run: SearchRun) -> list[dict]:
    persisted = (search_run.result_payload or {}).get("results")
    if isinstance(persisted, list):
        return persisted
    legacy = _stage_output(search_run, "web_search").get("results")
    return legacy if isinstance(legacy, list) else []


def _qualified_results(search_run: SearchRun) -> list[dict]:
    results = _stage_output(search_run, "supplier_qualification").get(
        "qualified_results"
    )
    return results if isinstance(results, list) else []


def _summary(search_run: SearchRun) -> SearchRunSummary:
    search_stage = next(
        (
            stage
            for stage in search_run.agent_runs
            if stage.agent_slug == "web_search"
        ),
        None,
    )
    planned = (
        (search_stage.input_payload or {}).get("queries")
        if search_stage is not None
        else []
    )
    if not isinstance(planned, list) or not planned:
        planner = _stage_output(search_run, "search_planner")
        planned = planner.get("queries")
    candidates = _candidate_results(search_run)
    qualified = _qualified_results(search_run)
    qualification_stage = next(
        (
            stage
            for stage in reversed(search_run.agent_runs)
            if stage.agent_slug == "supplier_qualification"
        ),
        None,
    )
    if qualification_stage is None:
        qualification_status = (
            "running"
            if search_run.status in {"fetching_sources", "qualifying"}
            else "not_started"
        )
    else:
        qualification_status = qualification_stage.status
    return SearchRunSummary(
        planned_query_count=len(planned) if isinstance(planned, list) else 0,
        executed_query_count=len(search_run.search_attempts),
        raw_page_count=sum(
            attempt.result_count or 0 for attempt in search_run.search_attempts
        ),
        candidate_count=len(candidates),
        qualified_count=len(qualified),
        manufacturer_candidate_count=sum(
            result.get("supplier_type") == "manufacturer"
            for result in qualified
            if isinstance(result, dict)
        ),
        qualification_status=qualification_status,
    )


def _list_item(
    search_run: SearchRun,
    *,
    queue_position: int | None = None,
) -> SearchRunListItem:
    item = SearchRunListItem.model_validate(search_run)
    item.owner_name = search_run.owner.full_name if search_run.owner else None
    item.queue_position = queue_position
    item.summary = _summary(search_run)
    item.result_count = item.summary.candidate_count
    return item


@router.get("", response_model=list[SearchRunListItem])
def list_search_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    rfq_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SearchRunListItem]:
    stmt = (
        select(SearchRun)
        .options(
            selectinload(SearchRun.owner),
            selectinload(SearchRun.agent_runs),
            selectinload(SearchRun.search_attempts),
        )
        .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if rfq_id is not None:
        stmt = stmt.where(SearchRun.rfq_id == rfq_id)
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(SearchRun.owner_id == user.id)
    runs = db.scalars(stmt).all()
    queued_ids = list(
        db.scalars(
            select(SearchRun.id)
            .where(
                SearchRun.mode == "queued_search",
                SearchRun.status == "queued",
            )
            .order_by(SearchRun.created_at, SearchRun.id)
        ).all()
    )
    queue_positions = {
        run_id: position for position, run_id in enumerate(queued_ids, start=1)
    }
    return [
        _list_item(run, queue_position=queue_positions.get(run.id))
        for run in runs
    ]


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
            selectinload(SearchRun.evidence_claims),
        )
    )
    if search_run is None or not _can_see(user, search_run):
        raise HTTPException(status_code=404, detail="Search run not found")
    item = SearchRunTrace.model_validate(search_run)
    item.owner_name = search_run.owner.full_name if search_run.owner else None
    if search_run.status == "queued":
        queued_ids = list(
            db.scalars(
                select(SearchRun.id)
                .where(
                    SearchRun.mode == "queued_search",
                    SearchRun.status == "queued",
                )
                .order_by(SearchRun.created_at, SearchRun.id)
            ).all()
        )
        try:
            item.queue_position = queued_ids.index(search_run.id) + 1
        except ValueError:
            item.queue_position = None
    item.summary = _summary(search_run)
    item.result_count = item.summary.candidate_count
    item.candidate_results = _candidate_results(search_run)
    item.qualified_results = _qualified_results(search_run)
    return item
