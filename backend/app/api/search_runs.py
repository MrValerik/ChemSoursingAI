"""User-visible supplier-search traces and safe task restarts."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import SearchRun, User
from app.models.enums import UserRole
from app.schemas.search_trace import (
    EvidenceClaimRead,
    SearchRunListItem,
    SearchRunRestartRead,
    SearchRunSummary,
    SearchRunTrace,
    SourceDocumentRead,
)
from app.services.search_trace import cancel_search_run, create_search_run, utc_now
from app.services.supplier_search_continuation import (
    candidate_results as continuation_candidate_results,
    country_runs,
    merge_unique_results,
    qualified_results as continuation_qualified_results,
    run_country,
    supplier_exclusions,
)

router = APIRouter(prefix="/search-runs", tags=["search-runs"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_STALE_AFTER = timedelta(minutes=30)


def _can_see(user: User, search_run: SearchRun) -> bool:
    return user.role in _SEE_ALL_ROLES or search_run.owner_id == user.id


def _stage_output(search_run: SearchRun, slug: str) -> dict:
    for stage in reversed(search_run.agent_runs):
        if stage.agent_slug == slug and isinstance(stage.output_payload, dict):
            return stage.output_payload
    return {}


def _candidate_results(search_run: SearchRun) -> list[dict]:
    return continuation_candidate_results(search_run)


def _qualified_results(search_run: SearchRun) -> list[dict]:
    return continuation_qualified_results(search_run)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _last_activity(search_run: SearchRun) -> datetime:
    timestamps = [search_run.started_at]
    for stage in search_run.agent_runs:
        timestamps.extend(
            value
            for value in (stage.started_at, stage.completed_at)
            if value is not None
        )
    for attempt in search_run.search_attempts:
        timestamps.extend(
            value
            for value in (attempt.started_at, attempt.completed_at)
            if value is not None
        )
    return max((_aware(value) for value in timestamps), default=utc_now())


def _is_stale(search_run: SearchRun) -> bool:
    return (
        search_run.status not in _TERMINAL_STATUSES
        and utc_now() - _last_activity(search_run) >= _STALE_AFTER
    )


def _can_restart(search_run: SearchRun) -> bool:
    return search_run.status in {"failed", "cancelled"} or _is_stale(search_run)


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
    item.is_stale = _is_stale(search_run)
    item.can_restart = _can_restart(search_run)
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
    merge_country: bool = Query(default=False),
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
        raise HTTPException(status_code=404, detail="Запуск поиска не найден")
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
    item.is_stale = _is_stale(search_run)
    item.can_restart = _can_restart(search_run)
    if merge_country and search_run.rfq_id is not None:
        country = run_country(search_run)
        if country:
            related_runs = [
                run
                for run in country_runs(
                    db,
                    rfq_id=search_run.rfq_id,
                    country=country,
                )
                if _can_see(user, run)
            ]
            if search_run.id not in {run.id for run in related_runs}:
                related_runs.insert(0, search_run)
            candidates, qualified = merge_unique_results(related_runs)
            item.candidate_results = candidates
            item.qualified_results = qualified
            item.source_documents = [
                SourceDocumentRead.model_validate(source)
                for run in related_runs
                for source in run.source_documents
            ]
            item.evidence_claims = [
                EvidenceClaimRead.model_validate(claim)
                for run in related_runs
                for claim in run.evidence_claims
            ]
            item.merged_run_count = len(related_runs)
            item.result_count = len(candidates)
            item.summary.candidate_count = len(candidates)
            item.summary.qualified_count = len(qualified)
            item.summary.manufacturer_candidate_count = sum(
                result.get("supplier_type") == "manufacturer"
                for result in qualified
            )
    return item


@router.post(
    "/{search_run_id}/restart",
    response_model=SearchRunRestartRead,
    status_code=202,
)
def restart_search_run(
    search_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SearchRunRestartRead:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
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
        raise HTTPException(status_code=404, detail="Запуск поиска не найден")
    if not _can_restart(search_run):
        raise HTTPException(
            status_code=409,
            detail=(
                "Задача ещё выполняется и получает обновления. "
                "Перезапуск станет доступен после 30 минут без прогресса."
            ),
        )

    country = run_country(search_run)
    related_runs: list[SearchRun] = []
    if search_run.rfq_id is not None and country:
        related_runs = country_runs(
            db,
            rfq_id=search_run.rfq_id,
            country=country,
            exclude_run_id=search_run.id,
        )
        active_newer = next(
            (
                run
                for run in related_runs
                if run.id > search_run.id
                and run.status not in _TERMINAL_STATUSES
            ),
            None,
        )
        if active_newer is not None:
            raise HTTPException(
                status_code=409,
                detail="Для этой страны уже есть более новая активная задача.",
            )

    payload = dict(search_run.input_payload or {})
    prior_domains, prior_names = supplier_exclusions(
        [search_run, *related_runs]
    )
    payload["excluded_supplier_domains"] = sorted(
        {
            *payload.get("excluded_supplier_domains", []),
            *prior_domains,
        }
    )[:500]
    payload["excluded_supplier_names"] = sorted(
        {
            *payload.get("excluded_supplier_names", []),
            *prior_names,
        }
    )[:500]
    payload["restart_of_search_run_id"] = search_run.id
    payload["continuation"] = {
        "previous_run_ids": [search_run.id, *[run.id for run in related_runs]],
        "excluded_supplier_count": len(
            {
                *payload["excluded_supplier_domains"],
                *payload["excluded_supplier_names"],
            }
        ),
    }

    cancel_search_run(
        search_run,
        reason="Выполнение остановлено: пользователь перезапустил задачу.",
    )
    restarted = create_search_run(
        db,
        owner_id=user.id,
        rfq_id=search_run.rfq_id,
        input_payload=payload,
        mode="queued_search",
        status="queued",
    )
    db.commit()
    queue_position = db.scalar(
        select(func.count(SearchRun.id)).where(
            SearchRun.mode == "queued_search",
            SearchRun.status == "queued",
            SearchRun.id <= restarted.id,
        )
    )
    return SearchRunRestartRead(
        search_run_id=restarted.id,
        status="queued",
        queue_position=queue_position or 1,
    )
