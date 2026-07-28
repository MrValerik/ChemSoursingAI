"""API schemas for observable supplier-search runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class SourceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_run_id: int | None
    url: str
    final_url: str | None
    domain: str | None
    title: str | None
    content_type: str | None
    status: str
    http_status: int | None
    text_content: str | None
    content_hash: str | None
    retrieved_at: datetime
    error: str | None


class EvidenceClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_run_id: int
    source_document_id: int
    result_index: int
    claim_type: str
    claim_value: str
    support_status: str
    quote: str
    quote_verified: bool
    created_at: datetime


class SearchRunSummary(BaseModel):
    planned_query_count: int = 0
    executed_query_count: int = 0
    raw_page_count: int = 0
    candidate_count: int = 0
    qualified_count: int = 0
    manufacturer_candidate_count: int = 0
    qualification_status: str = "not_started"


class SearchRunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    rfq_id: int | None
    owner_name: str | None = None
    status: str
    mode: str
    input_payload: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None
    error: str | None
    queue_position: int | None = None
    result_count: int = 0
    is_stale: bool = False
    can_restart: bool = False
    summary: SearchRunSummary = Field(default_factory=SearchRunSummary)


class SearchRunTrace(SearchRunListItem):
    result_payload: dict[str, Any] | None
    agent_runs: list[AgentRunRead]
    search_attempts: list[SearchAttemptRead]
    source_documents: list[SourceDocumentRead]
    evidence_claims: list[EvidenceClaimRead]
    candidate_results: list[dict[str, Any]] = Field(default_factory=list)
    qualified_results: list[dict[str, Any]] = Field(default_factory=list)
    merged_run_count: int = 1


class SearchRunRestartRead(BaseModel):
    search_run_id: int
    status: str
    queue_position: int
