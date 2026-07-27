"""Single-slot durable worker for queued supplier searches.

PostgreSQL is the source of truth. A job is claimed with a row lock, so adding
more worker processes later will not execute the same search twice. Production
currently runs one worker because the local Qwen server has one inference slot.
"""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from time import sleep

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.supplier_search import (
    SupplierQualificationRequest,
    SupplierSearchRequest,
    execute_supplier_qualification,
    execute_supplier_search,
)
from app.core.db import SessionLocal, init_db
from app.models import SearchRun, User
from app.services.search_trace import utc_now

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"search_completed", "completed", "failed", "cancelled"}
_stop_requested = False


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        return str(detail)
    return str(exc)


def recover_interrupted_jobs(db: Session) -> int:
    """Fail jobs abandoned during an earlier worker/process shutdown."""
    interrupted = list(
        db.scalars(
            select(SearchRun).where(
                SearchRun.mode == "queued_search",
                SearchRun.status.not_in({"queued", *_TERMINAL_STATUSES}),
            )
        ).all()
    )
    for run in interrupted:
        run.status = "failed"
        run.error = (
            "Выполнение было прервано перезапуском worker. "
            "Создайте повторный поиск."
        )
        run.completed_at = utc_now()
    db.commit()
    return len(interrupted)


def claim_next_job(db: Session) -> int | None:
    stmt = (
        select(SearchRun)
        .where(
            SearchRun.mode == "queued_search",
            or_(
                SearchRun.status == "queued",
                and_(
                    SearchRun.status == "search_completed",
                    SearchRun.rfq_id.is_not(None),
                ),
            ),
        )
        .order_by(SearchRun.created_at, SearchRun.id)
        .limit(1)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    run = db.scalar(stmt)
    if run is None:
        db.commit()
        return None
    run.status = (
        "fetching_sources"
        if run.status == "search_completed"
        else "identifying"
    )
    run.error = None
    db.commit()
    return run.id


def process_next_job(
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    executor: Callable[..., dict] | None = None,
    qualifier: Callable[..., dict] | None = None,
) -> int | None:
    """Execute search and its automatic qualification as one queued job."""
    with session_factory() as db:
        run_id = claim_next_job(db)
    if run_id is None:
        return None

    with session_factory() as db:
        run = db.get(SearchRun, run_id)
        if run is None:
            return run_id
        user = db.get(User, run.owner_id)
        if user is None:
            run.status = "failed"
            run.error = "Владелец задачи больше не существует"
            run.completed_at = utc_now()
            db.commit()
            return run_id
        try:
            request = SupplierSearchRequest.model_validate(run.input_payload)
            persisted_result = (
                run.result_payload
                if run.status == "fetching_sources"
                and isinstance(run.result_payload, dict)
                and isinstance(run.result_payload.get("results"), list)
                else None
            )
            if persisted_result is not None:
                result = persisted_result
            else:
                run_executor = executor or execute_supplier_search
                result = run_executor(
                    request,
                    db,
                    user,
                    search_run=run,
                )
            run.result_payload = result
            db.commit()

            candidate_results = result.get("results")
            run_qualifier = qualifier or (
                execute_supplier_qualification if executor is None else None
            )
            if (
                run_qualifier is not None
                and isinstance(candidate_results, list)
                and candidate_results
            ):
                qualification_request = SupplierQualificationRequest(
                    search_run_id=run.id,
                    cas=request.cas,
                    name=request.name,
                    country=request.country,
                    additional_instructions=request.additional_instructions,
                    results=candidate_results[:5],
                )
                run_qualifier(qualification_request, db, user)
                run.result_payload = result
            elif executor is None and isinstance(candidate_results, list):
                # There is nothing to fetch or qualify. The full queued job is
                # nevertheless complete, rather than being left between steps.
                run.status = "completed"
                run.completed_at = utc_now()
            elif run.status not in _TERMINAL_STATUSES:
                run.status = "search_completed"
            db.commit()
        except Exception as exc:
            db.rollback()
            failed_run = db.get(SearchRun, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.error = _error_text(exc)
                failed_run.completed_at = utc_now()
                db.commit()
            logger.exception("Queued supplier search %s failed", run_id)
    return run_id


def _request_stop(*_: object) -> None:
    global _stop_requested
    _stop_requested = True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    with SessionLocal() as db:
        recovered = recover_interrupted_jobs(db)
    if recovered:
        logger.warning("Marked %s interrupted search jobs as failed", recovered)
    logger.info("Supplier search worker started with one execution slot")
    while not _stop_requested:
        processed = process_next_job()
        if processed is None:
            sleep(2)
    logger.info("Supplier search worker stopped")


if __name__ == "__main__":
    main()
