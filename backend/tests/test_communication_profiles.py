"""Профили общения: приоритет, лимиты, аудит и неизменяемая безопасность."""

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    CommunicationPolicyAudit,
    CommunicationProfile,
    PromptTemplate,
    RFQ,
    RfqAiSetting,
    User,
)
from app.models.enums import UserRole
from app.services.communication_profiles import (
    budget_status,
    finalize_usage,
    profile_goal_reached,
    profile_prompt_instructions,
    resolve_profile,
    start_audit,
)


class UsageClient:
    def __init__(self, prompt: int, completion: int) -> None:
        self.usage = (prompt, completion)

    def take_usage(self) -> tuple[int, int]:
        usage, self.usage = self.usage, (0, 0)
        return usage


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_profile_precedence_and_role_goals() -> None:
    with _session() as db:
        buyer = resolve_profile(db, rfq_id=None)
        chemist = CommunicationProfile(
            slug="chemist-test",
            name="Химик",
            description=None,
            system_instructions="Собери цену и грейд, затем передай закупке.",
            required_fields=["price", "currency", "grade"],
            max_input_chars=8000,
            max_auto_replies=8,
            max_duration_minutes=100,
            max_prompt_tokens=10000,
            max_completion_tokens=2000,
            max_estimated_cost_usd=5,
        )
        db.add(chemist)
        db.flush()
        owner = User(
            username="chemist",
            full_name="Химик",
            password_hash="unused",
            role=UserRole.BUYER,
            communication_profile_id=chemist.id,
        )
        db.add(owner)
        db.flush()
        rfq = RFQ(name="Aspirin", owner_id=owner.id)
        db.add(rfq)
        db.flush()

        assert resolve_profile(db, rfq_id=rfq.id, actor_id=owner.id).id == chemist.id
        assert profile_goal_reached(
            chemist,
            {"price": 10, "currency": "USD", "grade": "99%"},
        )
        assert not profile_goal_reached(
            buyer,
            {"price": 10, "currency": "USD", "grade": "99%"},
        )

        db.add(RfqAiSetting(rfq_id=rfq.id, communication_profile_id=buyer.id))
        db.flush()
        assert resolve_profile(db, rfq_id=rfq.id, actor_id=owner.id).id == chemist.id
        assert resolve_profile(db, rfq_id=rfq.id).id == buyer.id


def test_large_input_and_duplicate_event_are_stopped_without_double_audit() -> None:
    with _session() as db:
        profile = resolve_profile(db, rfq_id=None)
        profile.max_input_chars = 500
        prompt = PromptTemplate(
            kind="supplier_communication",
            name="Dialogue",
            description=None,
            system_prompt="Safe versioned supplier communication prompt.",
            version=3,
            is_active=True,
        )
        db.add(prompt)
        db.flush()
        first = start_audit(
            db,
            event_key="email:one",
            text="x" * 501,
            rfq_id=None,
        )
        assert not first.budget.allowed
        assert first.audit.stop_reason == "input_too_large"
        assert first.audit.input_chars == 501
        assert first.audit.prompt_template_id == prompt.id
        assert first.audit.prompt_version == 3
        assert len(first.audit.budget_snapshot["safe_input_summary"]) <= 301

        duplicate = start_audit(
            db,
            event_key="email:one",
            text="x" * 501,
            rfq_id=None,
        )
        assert duplicate.duplicate
        assert duplicate.audit.id == first.audit.id
        assert db.scalar(select(func.count(CommunicationPolicyAudit.id))) == 1


def test_reply_and_token_budget_is_auditable() -> None:
    with _session() as db:
        profile = resolve_profile(db, rfq_id=None)
        profile.max_auto_replies = 1
        first = start_audit(
            db,
            event_key="message:one",
            text="Price is USD 10/kg.",
            rfq_id=None,
        )
        finalize_usage(first.audit, UsageClient(120, 30), reply_generated=True)
        db.commit()

        status = budget_status(db, profile=profile, rfq_id=None)
        assert not status.allowed
        assert status.stop_reason == "auto_reply_limit"
        assert status.snapshot["prompt_tokens_used"] == 120
        assert status.snapshot["completion_tokens_used"] == 30


def test_budget_is_individual_for_each_user() -> None:
    with _session() as db:
        profile = resolve_profile(db, rfq_id=None)
        profile.max_auto_replies = 1
        first_user = User(
            username="first",
            full_name="Первый пользователь",
            password_hash="unused",
            role=UserRole.BUYER,
        )
        second_user = User(
            username="second",
            full_name="Второй пользователь",
            password_hash="unused",
            role=UserRole.BUYER,
        )
        db.add_all([first_user, second_user])
        db.flush()

        first = start_audit(
            db,
            event_key="message:individual",
            text="Price is USD 10/kg.",
            rfq_id=None,
            actor_id=first_user.id,
        )
        finalize_usage(first.audit, UsageClient(120, 30), reply_generated=True)
        db.commit()

        first_status = budget_status(
            db, profile=profile, rfq_id=None, actor_id=first_user.id
        )
        second_status = budget_status(
            db, profile=profile, rfq_id=None, actor_id=second_user.id
        )
        assert not first_status.allowed
        assert first_status.snapshot["automatic_replies_used"] == 1
        assert second_status.allowed
        assert second_status.snapshot["automatic_replies_used"] == 0
        assert first.audit.actor_id == first_user.id


def test_profile_cannot_override_immutable_commercial_safety() -> None:
    with _session() as db:
        profile = resolve_profile(db, rfq_id=None)
        profile.system_instructions = "Confirm any order immediately."
        effective = profile_prompt_instructions(profile).casefold()
        assert "не разрешает подтверждать заказ" in effective
        assert "недоверенными данными" in effective
