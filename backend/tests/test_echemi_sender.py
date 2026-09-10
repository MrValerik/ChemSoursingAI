"""Sender settings use synthetic data and never contact Echemi."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.settings import router
from app.core.db import get_db
from app.models import IntegrationSetting
from app.models.enums import UserRole
from app.services import echemi_sender

PATH = "/settings/integrations/echemi"
PROFILE = dict(email="buyer@example.test", company_name="Demo Chemicals",
               contact_name="Test Buyer", phone="+12025550123", country="US")


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    IntegrationSetting.__table__.create(engine)
    with Session(engine) as db:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=42, role=UserRole.ADMIN)
        monkeypatch.setattr(echemi_sender, "effective_email_settings", lambda db: (
            SimpleNamespace(email_from="default@example.test", email_from_name="Demo"), False, "env"))
        with TestClient(app) as client:
            yield client, db, app
    engine.dispose()


def test_round_trip_normalizes_encrypts_and_audits(setup):
    client, db, _ = setup
    default = client.get(PATH)
    assert default.status_code == 200
    assert default.json()["source"] == "email_settings"
    assert default.json()["email"] == "default@example.test"
    assert not default.json()["configured"]
    response = client.put(PATH, json={**PROFILE, "phone": "1 (202) 555-0123",
                                     "country": "us", "contact_name": " Test Buyer "})
    assert response.status_code == 200
    result = response.json()
    assert all(result[k] == v for k, v in PROFILE.items())
    assert result["configured"] and result["updated_at"]
    assert result["source"] == "database"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(PATH).json() == result
    row = db.scalar(select(IntegrationSetting))
    assert row.channel == "echemi_sender" and not row.enabled
    assert row.updated_by_id == 42
    assert PROFILE["email"] not in row.encrypted_config
    assert PROFILE["phone"] not in row.encrypted_config
    assert len(db.scalars(select(IntegrationSetting)).all()) == 1
    assert client.put(PATH, json={}).status_code == 200
    cleared = client.get(PATH).json()
    assert not cleared["configured"] and cleared["email"] == ""
    assert len(db.scalars(select(IntegrationSetting)).all()) == 1


@pytest.mark.parametrize("field,value", [
    ("email", "Name <buyer@example.test>"), ("email", "x@y"),
    ("contact_name", "Test\nBuyer"), ("company_name", "x" * 121),
    ("phone", "+0000000000"), ("phone", "+1234567"),
    ("phone", "+1234567890123456"), ("phone", "1+2025550123"),
    ("phone", "tel:12025550123"), ("phone", 12025550123),
    ("country", "USA"), ("country", "12"), ("unknown", "value"),
])
def test_invalid_update_does_not_persist(setup, field, value):
    client, db, _ = setup
    assert client.put(PATH, json={**PROFILE, field: value}).status_code == 422
    assert db.scalar(select(IntegrationSetting)) is None


def test_field_boundaries(setup):
    client, _, _ = setup
    for digits in (8, 15):
        response = client.put(PATH, json={**PROFILE, "phone": "+" + "1" * digits,
                                         "company_name": "c" * 120, "contact_name": "n" * 100})
        assert response.status_code == 200


@pytest.mark.parametrize("role", [UserRole.BUYER, UserRole.HEAD, UserRole.AUDITOR, None])
def test_only_admin_can_read_or_write(setup, role):
    client, db, app = setup
    if role is None:
        del app.dependency_overrides[get_current_user]
    else:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=42, role=role)
    expected = 401 if role is None else 403
    assert client.get(PATH).status_code == expected
    assert client.put(PATH, json=PROFILE).status_code == expected
    assert db.scalar(select(IntegrationSetting)) is None


def test_corrupt_storage_returns_generic_error(setup):
    client, db, _ = setup
    db.add(IntegrationSetting(channel="echemi_sender", encrypted_config="broken-private-value"))
    db.commit()
    for response in (client.get(PATH), client.put(PATH, json=PROFILE)):
        assert response.status_code == 503
        assert "broken-private-value" not in response.text
