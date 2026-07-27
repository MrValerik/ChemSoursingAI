"""Persistent, user-visible trace of the supplier-search pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.prompt import PromptTemplate
    from app.models.user import User


class SearchRun(Base, TimestampMixin):
    """One end-to-end search request initiated by a user."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="expert")
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    owner: Mapped["User"] = relationship()
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="search_run",
        cascade="all, delete-orphan",
        order_by="AgentRun.sequence",
    )
    search_attempts: Mapped[list["SearchAttempt"]] = relationship(
        back_populates="search_run",
        cascade="all, delete-orphan",
        order_by="SearchAttempt.id",
    )
    source_documents: Mapped[list["SourceDocument"]] = relationship(
        back_populates="search_run",
        cascade="all, delete-orphan",
        order_by="SourceDocument.id",
    )


class AgentRun(Base, TimestampMixin):
    """A versioned LLM or deterministic stage within a search run."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    agent_slug: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(255))
    execution_type: Mapped[str] = mapped_column(String(32), default="llm")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)

    prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL"), default=None
    )
    prompt_version: Mapped[int | None] = mapped_column(Integer, default=None)
    effective_system_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    model: Mapped[str | None] = mapped_column(String(255), default=None)
    temperature: Mapped[float | None] = mapped_column(default=None)
    max_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    search_run: Mapped["SearchRun"] = relationship(back_populates="agent_runs")
    prompt: Mapped["PromptTemplate | None"] = relationship()
    search_attempts: Mapped[list["SearchAttempt"]] = relationship(
        back_populates="agent_run"
    )


class SearchAttempt(Base, TimestampMixin):
    """An exact query sent to a search connector."""

    __tablename__ = "search_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), default=None, index=True
    )
    connector: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16), default=None)
    source_type: Mapped[str | None] = mapped_column(String(64), default=None)
    purpose: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    result_count: Mapped[int | None] = mapped_column(Integer, default=None)
    results_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, default=None
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    search_run: Mapped["SearchRun"] = relationship(back_populates="search_attempts")
    agent_run: Mapped["AgentRun | None"] = relationship(
        back_populates="search_attempts"
    )


class SourceDocument(Base, TimestampMixin):
    """A bounded snapshot of a primary page used by the pipeline."""

    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), default=None, index=True
    )
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text, default=None)
    domain: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    title: Mapped[str | None] = mapped_column(Text, default=None)
    content_type: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    text_content: Mapped[str | None] = mapped_column(Text, default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text, default=None)

    search_run: Mapped["SearchRun"] = relationship(back_populates="source_documents")
    agent_run: Mapped["AgentRun | None"] = relationship()
