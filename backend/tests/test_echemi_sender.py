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
    assert all(result[k] == v for k, v in PROFILE.items() if k != "email")
    assert result["email"] == "default@example.test"
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
    assert not cleared["configured"] and cleared["email"] == "default@example.test"
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


@pytest.mark.parametrize("role", [UserRole.HEAD, UserRole.AUDITOR, UserRole.GUEST, None])
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


def test_buyer_profiles_are_private_and_other_integrations_stay_admin_only(setup):
    client, db, app = setup
    client.put(PATH, json=PROFILE)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=43, role=UserRole.BUYER)
    assert client.get(PATH).json()["email"] == "default@example.test"
    values = {**PROFILE, "city": "Boston", "address": "1 Example Street", "postal_code": "02101"}
    assert client.put(PATH, json=values).status_code == 200
    assert client.get(PATH).json()["city"] == "Boston"
    assert client.get("/settings/integrations/email").status_code == 403
    assert client.put("/settings/integrations/email", json={}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=44, role=UserRole.BUYER)
    assert client.get(PATH).json()["city"] == ""
    assert len(db.scalars(select(IntegrationSetting)).all()) == 2


@pytest.mark.parametrize("field,value", [("city", "a" * 101), ("address", "x\ny"),
                                        ("postal_code", "x" * 21), ("whatsapp", "123")])
def test_extended_fields_validate(setup, field, value):
    assert setup[0].put(PATH, json={**PROFILE, field: value}).status_code == 422


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.BUYER])
def test_reply_email_always_follows_mail_settings_including_legacy_profiles(setup, monkeypatch, role):
    from app.services.integration_settings import save_setting, _decrypt
    client, db, app = setup
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=42, role=role)
    channel = "echemi_sender_42" if role == UserRole.BUYER else "echemi_sender"
    save_setting(db, channel=channel, enabled=False, payload=PROFILE, actor_id=42)
    assert client.get(PATH).json()["email"] == "default@example.test"
    response = client.put(PATH, json={**PROFILE, "email": "override@example.test"})
    assert response.status_code == 200
    assert response.json()["email"] == "default@example.test"
    assert "email" not in _decrypt(db.scalar(select(IntegrationSetting)).encrypted_config)
    for address in ("changed@example.test", ""):
        monkeypatch.setattr(echemi_sender, "effective_email_settings", lambda db: (
            SimpleNamespace(email_from=address, email_from_name="Demo"), False, "database"))
        result = client.get(PATH).json()
        assert result["email"] == address
        assert result["configured"] == bool(address)
