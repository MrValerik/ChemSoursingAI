"""Exercise the real authentication, routing and DB isolation boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_auth.db")

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import db as database
from app.core.config import get_settings
from app.core.guest import guest_session
from app.core.security import hash_password
from app.main import create_app
from app.models import Base, RFQ, User
from app.models.enums import UserRole


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, username="private-user", full_name="Private user",
                    password_hash=hash_password("private-password"), role=UserRole.ADMIN))
        db.add(RFQ(id=1, name="CONFIDENTIAL REQUEST", cas="50-78-2", owner_id=1, incoterms=["FCA"]))
        db.add(RFQ(id=900, name="PRIVATE RFQ 900", owner_id=1))
        db.commit()
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(get_settings(), "guest_access_enabled", True)
    # No lifespan: the test's production DB deliberately contains only private rows.
    yield TestClient(create_app())
    engine.dispose()


def guest_headers(client):
    response = client.post("/auth/guest")
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "guest"
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_guest_reads_standard_workspace_without_production_access(client, monkeypatch):
    headers = guest_headers(client)
    def forbidden():
        pytest.fail("Guest attempted to open the production DB")
    monkeypatch.setattr(database, "SessionLocal", forbidden)
    for url in ["/auth/me", "/rfq", "/rfq/1", "/suppliers", "/rfq/1/recipients",
                "/rfq/1/communications", "/rfq/1/quotations", "/rfq/1/summary",
                "/rfq/1/purchase-decision", "/rfq/1/purchase-history",
                "/search-runs", "/search-runs/1", "/search-runs/1?merge_country=true",
                "/rfq/2", "/rfq/3", "/substances/price-history?cas=50-78-2"]:
        response = client.get(url, headers=headers)
        assert response.status_code == 200, (url, response.text)
        assert "CONFIDENTIAL" not in response.text
    assert len(client.get("/rfq", headers=headers).json()) == 3
    assert client.get("/rfq/900", headers=headers).status_code == 404
    assert client.get("/search-runs/900", headers=headers).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("POST", "/rfq"), ("DELETE", "/rfq/1"), ("PATCH", "/rfq/1"),
    ("POST", "/rfq/1/communications/send"), ("POST", "/rfq/1/communications/translation"),
    ("POST", "/quotations"), ("PUT", "/rfq/1/purchase-decision"),
    ("POST", "/suppliers"), ("POST", "/search-runs/1/restart"),
    ("PUT", "/settings/preferences"), ("GET", "/settings/integrations/email"),
    ("GET", "/users"), ("GET", "/mail/messages"), ("GET", "/documents/1/download"),
    ("GET", "/substances/verify?cas=50-78-2"), ("GET", "/health/llm"),
    ("POST", "/auth/login"), ("GET", "/not-yet-reviewed-endpoint"),
])
def test_guest_rejects_writes_private_reads_and_external_actions(client, method, path):
    headers = guest_headers(client)
    assert client.request(method, path, headers=headers).status_code == 403
    with database.SessionLocal() as db:
        assert db.get(RFQ, 1).name == "CONFIDENTIAL REQUEST"


def test_guest_expiry_tampering_and_disabled_access(client, monkeypatch):
    headers = guest_headers(client)
    token = headers["Authorization"].split()[1]
    payload = jwt.decode(token, get_settings().auth_secret_key, algorithms=["HS256"])
    payload["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired = jwt.encode(payload, get_settings().auth_secret_key, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + expired}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    monkeypatch.setattr(get_settings(), "guest_access_enabled", False)
    assert client.post("/auth/guest").status_code == 403
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_guest_connections_are_read_only_and_concurrent(client):
    headers = guest_headers(client)
    with guest_session() as db:
        with pytest.raises(OperationalError):
            db.execute(text("DELETE FROM rfqs"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.get("/rfq", headers=headers), range(8)))
    assert all(r.status_code == 200 and len(r.json()) == 3 for r in results)


def test_guest_cannot_control_browser_over_websocket(client):
    headers = guest_headers(client)
    with client.websocket_connect("/echemi-searches/1/manual") as ws:
        ws.send_text(headers["Authorization"].split()[1])
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_text()
        assert error.value.code == 4403


def test_normal_login_is_unchanged_and_guest_role_is_not_assignable(client):
    response = client.post("/auth/login", json={"username": "private-user", "password": "private-password"})
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/rfq/1", headers=headers).json()["name"] == "CONFIDENTIAL REQUEST"
    assert client.patch("/users/1", headers=headers, json={"role": "guest"}).status_code == 422
    assert client.post("/users", headers=headers, json={"username": "test", "full_name": "Test", "password": "123456", "role": "guest"}).status_code == 422
    assert client.get("/rfq").status_code == 401
