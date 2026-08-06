"""Helpers that make every search stage persistently observable."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from app.core.search_contracts import (
    AGENT_CONTRACT_VERSIONS,
    SUPPLIER_SEARCH_GRAPH_VERSION,
)
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
    graph_version: str = SUPPLIER_SEARCH_GRAPH_VERSION,
) -> SearchRun:
    run = SearchRun(
        owner_id=owner_id,
        rfq_id=rfq_id,
        status=status,
        mode=mode,
        graph_version=graph_version,
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
    contract_version: str | None = None,
    input_payload: dict[str, Any] | None = None,
    prompt: PromptTemplate | None = None,
    effective_system_prompt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[AgentRun, float]:
    resolved_contract_version = contract_version or AGENT_CONTRACT_VERSIONS.get(
        agent_slug
    )
    if resolved_contract_version is None:
        raise ValueError(
            f"Неизвестна версия контракта этапа {agent_slug!r}"
        )
    agent_run = AgentRun(
        search_run_id=search_run.id,
        sequence=sequence,
        agent_slug=agent_slug,
        agent_name=agent_name,
        execution_type=execution_type,
        contract_version=resolved_contract_version,
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


def log_agent_event(
    agent_run: AgentRun,
    message: str,
    *,
    kind: str = "action",
    data: dict[str, Any] | None = None,
) -> None:
    """Append a user-readable activity entry to the stage's live log.

    The JSON column is reassigned (not mutated) so SQLAlchemy tracks the
    change; the caller's next commit makes the entry visible to the polling
    UI while the stage is still running.
    """
    entry: dict[str, Any] = {
        "at": utc_now().isoformat(),
        "kind": kind,
        "message": message,
    }
    if data:
        entry["data"] = data
    agent_run.events = [*(agent_run.events or []), entry]


def finish_agent_run(
    agent_run: AgentRun,
    started_clock: float,
    *,
    output_payload: dict[str, Any] | None = None,
    raw_output_payload: dict[str, Any] | None = None,
    parsed_output_payload: dict[str, Any] | None = None,
    validation_output_payload: dict[str, Any] | None = None,
    policy_output_payload: dict[str, Any] | None = None,
    error: str | None = None,
    llm: Any | None = None,
) -> None:
    if llm is not None:
        # Расход токенов этапа. Считать стоимость иначе можно только
        # повтором вызовов, то есть заплатив за замер второй раз. Берётся
        # прирост, а не весь счётчик: клиент живёт дольше одного этапа.
        take = getattr(llm, "take_usage", None)
        if callable(take):
            prompt, completion = take()
            agent_run.prompt_tokens = prompt or None
            agent_run.completion_tokens = completion or None
    agent_run.output_payload = output_payload
    agent_run.raw_output_payload = raw_output_payload
    agent_run.parsed_output_payload = parsed_output_payload
    agent_run.validation_output_payload = validation_output_payload
    agent_run.policy_output_payload = policy_output_payload
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


def cancel_search_run(
    search_run: SearchRun,
    *,
    reason: str,
) -> None:
    """Cancel a non-terminal run and close its running trace parts."""
    terminal_statuses = {"completed", "failed", "cancelled"}
    if search_run.status in terminal_statuses:
        return

    now = utc_now()
    search_run.status = "cancelled"
    search_run.error = reason
    search_run.completed_at = now

    for stage in search_run.agent_runs:
        if stage.status != "running":
            continue
        stage.status = "failed"
        stage.error = reason
        stage.completed_at = now
        started_at = stage.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=now.tzinfo)
        stage.latency_ms = max(
            0,
            round((now - started_at).total_seconds() * 1000),
        )

    for attempt in search_run.search_attempts:
        if attempt.status != "running":
            continue
        attempt.status = "failed"
        attempt.error = reason
        attempt.completed_at = now
        started_at = attempt.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=now.tzinfo)
        attempt.latency_ms = max(
            0,
            round((now - started_at).total_seconds() * 1000),
        )

    for source in search_run.source_documents:
        if source.status == "running":
            source.status = "failed"
            source.error = reason
