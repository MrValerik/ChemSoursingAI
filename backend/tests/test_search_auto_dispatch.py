from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import user_preferences, settings
from app.api.deps import get_current_user
from app.core.config import Settings
from app.core.db import get_db
from app.models import Base, Communication, Manager, RFQ, RfqRecipient, RfqSupplierLink, SearchRun, Supplier, User
from app.models.enums import DispatchStatus, UserRole
from app.services import combined_communication, search_auto_dispatch
from app.connectors.email import EmailDeliveryError


@pytest.fixture
def scenario(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="auto-test", full_name="Test", password_hash="unused", role=UserRole.BUYER)
        supplier = Supplier(company="Synthetic Manufacturer")
        db.add_all([user, supplier])
        db.flush()
        manager = Manager(supplier_id=supplier.id, email="sales@example.test")
        rfq = RFQ(name="Glycerol", cas="56-81-5", owner_id=user.id)
        db.add_all([manager, rfq])
        db.flush()
        run = SearchRun(owner_id=user.id, rfq_id=rfq.id, status="completed", input_payload={}, started_at=datetime.now(timezone.utc))
        db.add(run)
        db.flush()
        link = RfqSupplierLink(rfq_id=rfq.id, supplier_id=supplier.id, search_run_id=run.id)
        db.add(link)
        db.commit()
        monkeypatch.setattr(search_auto_dispatch, "prepare_rfq_english_text", lambda rfq: None)
        monkeypatch.setattr(search_auto_dispatch, "render_rfq_text", lambda rfq: ("Quotation", "Please quote Glycerol, CAS 56-81-5."))
        monkeypatch.setattr(combined_communication, "effective_email_settings", lambda db: (Settings(email_delivery_mode="demo"), False, "test"))
        yield db, user, supplier, manager, rfq, run, link
    engine.dispose()


def execute(scenario, *, eligible=True):
    db, _, supplier, _, _, run, _ = scenario
    search_auto_dispatch.auto_dispatch_after_search(
        db, search_run=run,
        results=[{"result_index": 0, "shortlist_eligible": eligible, "verification": {"status": "confirmed"}}],
        registry_links=[{"result_index": 0, "supplier_id": supplier.id}],
    )


@pytest.mark.parametrize("role", list(UserRole))
def test_preferences_are_personal_and_admin_settings_stay_protected(scenario, role):
    db, user, *_ = scenario
    user.role = role
    other = User(username="other", full_name="Other", password_hash="unused")
    db.add(other)
    db.commit()
    app = FastAPI()
    app.include_router(user_preferences.router)
    app.include_router(settings.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        assert client.get("/settings/preferences").json() == {"auto_dispatch_after_search": False}
        assert client.put("/settings/preferences", json={"auto_dispatch_after_search": True}).json() == {"auto_dispatch_after_search": True}
        assert client.get("/settings/preferences").json()["auto_dispatch_after_search"] is True
        db.refresh(other)
        assert other.auto_dispatch_after_search is False
        assert client.put("/settings/preferences", json={"auto_dispatch_after_search": "false"}).status_code == 422
        assert client.put("/settings/preferences", json={"auto_dispatch_after_search": True, "user_id": other.id}).status_code == 422
        if role != UserRole.ADMIN:
            assert client.get("/settings/integrations/email").status_code == 403
        assert client.put("/settings/preferences", json={"auto_dispatch_after_search": False}).json()["auto_dispatch_after_search"] is False


def test_preferences_require_login():
    app = FastAPI()
    app.include_router(user_preferences.router)
    with TestClient(app) as client:
        assert client.get("/settings/preferences").status_code == 401
        assert client.put("/settings/preferences", json={"auto_dispatch_after_search": True}).status_code == 401


def test_default_off_and_demo_send_once(scenario):
    db, user, _, _, _, run, _ = scenario
    execute(scenario)
    assert db.scalar(select(Communication)) is None
    user.auto_dispatch_after_search = True
    db.commit()
    execute(scenario)
    execute(scenario)
    messages = list(db.scalars(select(Communication)))
    assert len(messages) == 1
    assert messages[0].status == "demo"
    assert messages[0].idempotency_key.startswith("dispatch-")
    assert db.scalar(select(RfqRecipient)).status == DispatchStatus.SENT
    assert run.status == "completed"


@pytest.mark.parametrize("block", ["failed", "cancelled", "running", "replay", "auditor", "inactive", "excluded", "rejected", "no_email", "unverified", "other_owner", "deleted", "analog"])
def test_unsafe_or_incomplete_search_does_not_send(scenario, block):
    db, user, supplier, manager, rfq, run, link = scenario
    user.auto_dispatch_after_search = True
    if block in {"failed", "cancelled", "running"}: run.status = block
    if block == "replay": run.replay_mode = "stored"
    if block == "auditor": user.role = UserRole.AUDITOR
    if block == "inactive": user.is_active = False
    if block == "excluded": link.status = "excluded"
    if block == "rejected": supplier.qualification_status = "rejected"
    if block == "no_email": manager.email = None
    if block == "other_owner": rfq.owner_id = 999
    if block == "deleted": rfq.deleted_at = datetime.now(timezone.utc)
    if block == "analog": rfq.identification_method = "analog"
    db.commit()
    execute(scenario, eligible=block != "unverified")
    assert db.scalar(select(Communication)) is None
    assert db.scalar(select(RfqRecipient)) is None


@pytest.mark.parametrize("failure", [False, True])
def test_live_delivery_and_error_are_not_repeated(scenario, monkeypatch, failure):
    db, user, _, _, _, run, _ = scenario
    user.auto_dispatch_after_search = True
    db.commit()
    calls = []
    monkeypatch.setattr(combined_communication, "effective_email_settings", lambda db: (Settings(email_delivery_mode="live", email_from="buyer@example.test"), True, "test"))
    def send(self, **kwargs):
        calls.append(kwargs)
        if failure: raise EmailDeliveryError("Synthetic SMTP failure")
        return "<test-message@example.test>"
    monkeypatch.setattr(combined_communication.EmailConnector, "send", send)
    execute(scenario)
    execute(scenario)
    assert len(calls) == 1
    message = db.scalar(select(Communication))
    assert message.status == ("delivery_error" if failure else "sent")
    assert run.status == "completed"


def test_preparation_error_preserves_search(scenario, monkeypatch):
    db, user, _, _, _, run, _ = scenario
    user.auto_dispatch_after_search = True
    db.commit()
    def fail(rfq): raise ValueError("Synthetic preparation error")
    monkeypatch.setattr(search_auto_dispatch, "prepare_rfq_english_text", fail)
    execute(scenario)
    assert run.status == "completed"
    assert run.input_payload["auto_dispatch"]["status"] == "error"
    assert db.scalar(select(Communication)) is None
