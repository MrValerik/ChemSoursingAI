"""API schemas for observable supplier-search runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    agent_slug: str
    agent_name: str
    execution_type: str
    status: str
    prompt_id: int | None
    prompt_version: int | None
    effective_system_prompt: str | None
    input_payload: dict[str, Any] | None
    output_payload: dict[str, Any] | None
    model: str | None
    temperature: float | None
    max_tokens: int | None
    started_at: datetime
    completed_at: datetime | None
    latency_ms: int | None
    error: str | None


class SearchAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_run_id: int | None
    connector: str
    query: str
    language: str | None
    source_type: str | None
    purpose: str | None
    status: str
    result_count: int | None
    results_payload: list[dict[str, Any]] | None
    started_at: datetime
    completed_at: datetime | None
    latency_ms: int | None
    error: str | None


class SearchRunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    owner_name: str | None = None
    status: str
    mode: str
    input_payload: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None
    error: str | None


class SearchRunTrace(SearchRunListItem):
    agent_runs: list[AgentRunRead]
    search_attempts: list[SearchAttemptRead]
