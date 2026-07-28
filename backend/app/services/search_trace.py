"""Helpers that make every search stage persistently observable."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentRun, PromptTemplate, SearchAttempt, SearchRun


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_search_run(
    db: Session,
    *,
    owner_id: int,
    input_payload: dict[str, Any],
    rfq_id: int | None = None,
    mode: str = "expert",
    status: str = "running",
) -> SearchRun:
    run = SearchRun(
        owner_id=owner_id,
        rfq_id=rfq_id,
        status=status,
        mode=mode,
        input_payload=input_payload,
        started_at=utc_now(),
    )
    db.add(run)
    db.flush()
    return run


def start_agent_run(
    db: Session,
    *,
    search_run: SearchRun,
    sequence: int,
    agent_slug: str,
    agent_name: str,
    execution_type: str,
    input_payload: dict[str, Any] | None = None,
    prompt: PromptTemplate | None = None,
    effective_system_prompt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[AgentRun, float]:
    agent_run = AgentRun(
        search_run_id=search_run.id,
        sequence=sequence,
        agent_slug=agent_slug,
        agent_name=agent_name,
        execution_type=execution_type,
        status="running",
        prompt_id=prompt.id if prompt else None,
        prompt_version=prompt.version if prompt else None,
        effective_system_prompt=effective_system_prompt,
        input_payload=input_payload,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        started_at=utc_now(),
    )
    db.add(agent_run)
    db.flush()
    return agent_run, monotonic()


def finish_agent_run(
    agent_run: AgentRun,
    started_clock: float,
    *,
    output_payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    agent_run.output_payload = output_payload
    agent_run.error = error
    agent_run.status = "failed" if error else "completed"
    agent_run.completed_at = utc_now()
    agent_run.latency_ms = round((monotonic() - started_clock) * 1000)


def start_search_attempt(
    db: Session,
    *,
    search_run: SearchRun,
    query: str,
    connector: str,
    agent_run: AgentRun | None = None,
    language: str | None = None,
    source_type: str | None = None,
    purpose: str | None = None,
) -> tuple[SearchAttempt, float]:
    attempt = SearchAttempt(
        search_run_id=search_run.id,
        agent_run_id=agent_run.id if agent_run else None,
        connector=connector,
        query=query,
        language=language,
        source_type=source_type,
        purpose=purpose,
        status="running",
        started_at=utc_now(),
    )
    db.add(attempt)
    db.flush()
    return attempt, monotonic()


def finish_search_attempt(
    attempt: SearchAttempt,
    started_clock: float,
    *,
    result_count: int | None = None,
    results_payload: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> None:
    attempt.result_count = result_count
    attempt.results_payload = results_payload
    attempt.error = error
    attempt.status = "failed" if error else "completed"
    attempt.completed_at = utc_now()
    attempt.latency_ms = round((monotonic() - started_clock) * 1000)


def finish_search_run(
    search_run: SearchRun,
    *,
    error: str | None = None,
) -> None:
    search_run.error = error
    search_run.status = "failed" if error else "completed"
    search_run.completed_at = utc_now()
