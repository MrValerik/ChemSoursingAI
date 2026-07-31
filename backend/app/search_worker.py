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
    SearchRunCancelled,
    SupplierQualificationRequest,
    SupplierSearchRequest,
    execute_supplier_qualification,
    execute_supplier_search,
)
from app.core.db import SessionLocal, init_db
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.models import SearchRun, User
from app.services.search_trace import utc_now
from app.services.supplier_search_continuation import (
    country_runs,
    supplier_exclusions,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"search_completed", "completed", "failed", "cancelled"}
_MAX_LLM_AUTO_RETRIES = 3
_stop_requested = False


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        return str(detail)
    return str(exc)


def _is_llm_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, LLMUnavailableError):
        return True
    return isinstance(exc, HTTPException) and exc.status_code == 503


def requeue_after_llm_outage(
    db: Session, run: SearchRun, exc: Exception
) -> bool:
    """Return a job to the queue instead of failing on a transient LLM outage.

    The worker refuses to claim jobs until the local model reports healthy,
    so the requeued job simply waits for the model to come back. A bounded
    retry counter keeps a persistent outage from looping forever. Saved
    search results let the job resume from fetching/qualification without
    repeating the web search.
    """
    if not _is_llm_unavailable_error(exc):
        return False
    payload = dict(run.input_payload or {})
    retries = int(payload.get("llm_auto_retry_count") or 0)
    if retries >= _MAX_LLM_AUTO_RETRIES:
        return False
    has_search_results = (
        run.rfq_id is not None
        and isinstance(run.result_payload, dict)
        and bool(run.result_payload.get("results"))
    )
    payload["llm_auto_retry_count"] = retries + 1
    payload["last_llm_outage"] = _error_text(exc)[:500]
    run.input_payload = payload
    run.status = "search_completed" if has_search_results else "queued"
    run.error = None
    run.completed_at = None
    db.commit()
    return True


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
    run.completed_at = None
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
            payload = dict(run.input_payload or {})
            country = payload.get("country")
            if run.rfq_id is not None and isinstance(country, str):
                prior_runs = country_runs(
                    db,
                    rfq_id=run.rfq_id,
                    country=country,
                    exclude_run_id=run.id,
                )
                prior_domains, prior_names = supplier_exclusions(prior_runs)
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
                payload["continuation"] = {
                    "previous_run_ids": [item.id for item in prior_runs],
                    "excluded_supplier_count": len(
                        {
                            *payload["excluded_supplier_domains"],
                            *payload["excluded_supplier_names"],
                        }
                    ),
                }
                run.input_payload = payload
                db.commit()
            request = SupplierSearchRequest.model_validate(payload)
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
            reserve_results = result.get("reserve_results")
            qualification_pool = (
                [
                    *candidate_results,
                    *(reserve_results if isinstance(reserve_results, list) else []),
                ]
                if isinstance(candidate_results, list)
                else []
            )
            run_qualifier = qualifier or (
                execute_supplier_qualification if executor is None else None
            )
            if (
                run_qualifier is not None
                and qualification_pool
            ):
                qualification_request = SupplierQualificationRequest(
                    search_run_id=run.id,
                    cas=request.cas,
                    name=request.name,
                    country=request.country,
                    additional_instructions=request.additional_instructions,
                    expert_notes=request.catalog_notes,
                    target_count=request.limit,
                    results=qualification_pool[:60],
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
            if (
                failed_run is not None
                and failed_run.status != "cancelled"
                and not isinstance(exc, SearchRunCancelled)
            ):
                if requeue_after_llm_outage(db, failed_run, exc):
                    logger.warning(
                        "Queued supplier search %s hit an LLM outage; "
                        "returned to the queue (retry %s of %s)",
                        run_id,
                        failed_run.input_payload.get("llm_auto_retry_count"),
                        _MAX_LLM_AUTO_RETRIES,
                    )
                    return run_id
                failed_run.status = "failed"
                failed_run.error = _error_text(exc)
                failed_run.completed_at = utc_now()
                db.commit()
            if isinstance(exc, SearchRunCancelled):
                logger.info("Queued supplier search %s was cancelled", run_id)
            else:
                logger.exception("Queued supplier search %s failed", run_id)
    return run_id


def process_ready_job(
    *,
    readiness_checker: Callable[[], tuple[bool, str | None]] | None = None,
    processor: Callable[[], int | None] = process_next_job,
) -> tuple[bool, int | None]:
    """Process one job only after the local model reports readiness."""
    check = readiness_checker or LLMClient().check_health
    ready, _ = check()
    if not ready:
        return False, None
    return True, processor()


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
    waiting_for_llm = False
    while not _stop_requested:
        ready, processed = process_ready_job()
        if not ready:
            if not waiting_for_llm:
                logger.info(
                    "Local LLM is not ready; queued jobs remain untouched"
                )
            waiting_for_llm = True
            sleep(5)
            continue
        if waiting_for_llm:
            logger.info("Local LLM is ready; queue processing resumed")
            waiting_for_llm = False
        if processed is None:
            sleep(2)
    logger.info("Supplier search worker stopped")


if __name__ == "__main__":
    main()
