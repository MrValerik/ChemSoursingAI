"""Версионированные профили общения и аудит безопасных лимитов диалога."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CommunicationProfile(Base, TimestampMixin):
    __tablename__ = "communication_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    system_instructions: Mapped[str] = mapped_column(Text)
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    max_input_chars: Mapped[int] = mapped_column(Integer, default=12000)
    max_auto_replies: Mapped[int] = mapped_column(Integer, default=12)
    max_duration_minutes: Mapped[int] = mapped_column(Integer, default=10080)
    max_prompt_tokens: Mapped[int] = mapped_column(Integer, default=60000)
    max_completion_tokens: Mapped[int] = mapped_column(Integer, default=12000)
    max_estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("10.0000")
    )
    updated_by: Mapped[str | None] = mapped_column(String(255), default=None)


class CommunicationProfileVersion(Base, TimestampMixin):
    __tablename__ = "communication_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_profile_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("communication_profiles.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    system_instructions: Mapped[str] = mapped_column(Text)
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_input_chars: Mapped[int] = mapped_column(Integer)
    max_auto_replies: Mapped[int] = mapped_column(Integer)
    max_duration_minutes: Mapped[int] = mapped_column(Integer)
    max_prompt_tokens: Mapped[int] = mapped_column(Integer)
    max_completion_tokens: Mapped[int] = mapped_column(Integer)
    max_estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    changed_by: Mapped[str | None] = mapped_column(String(255), default=None)


class CommunicationPolicyAudit(Base, TimestampMixin):
    """Один сохранённый policy/budget результат на одно входящее событие."""

    __tablename__ = "communication_policy_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id", ondelete="SET NULL"), index=True, default=None
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("managers.id", ondelete="SET NULL"), index=True, default=None
    )
    communication_id: Mapped[int | None] = mapped_column(
        ForeignKey("communications.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("communication_test_runs.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("communication_profiles.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    profile_slug: Mapped[str] = mapped_column(String(64))
    profile_name: Mapped[str] = mapped_column(String(255))
    profile_version: Mapped[int] = mapped_column(Integer)
    prompt_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL"), default=None
    )
    prompt_version: Mapped[int | None] = mapped_column(Integer, default=None)
    policy_route: Mapped[str] = mapped_column(String(32), default="pending")
    policy_category: Mapped[str] = mapped_column(String(64), default="unclear")
    policy_explanation: Mapped[str] = mapped_column(Text, default="")
    policy_method: Mapped[str] = mapped_column(String(32), default="rule")
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    automatic_replies_used: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal("0")
    )
    reply_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    budget_snapshot: Mapped[dict | None] = mapped_column(JSON, default=None)
