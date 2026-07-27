"""Persistent search traces are complete and respect project RBAC."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_runs.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import User
from app.services.search_trace import (
    create_search_run,
    finish_agent_run,
    finish_search_attempt,
    finish_search_run,
    start_agent_run,
    start_search_attempt,
)


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_search_runs.db"):
        os.remove("test_search_runs.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_search_runs.db"):
        os.remove("test_search_runs.db")


def _auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _build_completed_trace() -> int:
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "50-78-2", "name": "Aspirin"},
        )
        agent, agent_clock = start_agent_run(
            db,
            search_run=run,
            sequence=1,
            agent_slug="search_planner",
            agent_name="Планировщик поиска",
            execution_type="llm",
            input_payload={"cas": "50-78-2"},
            effective_system_prompt="Use the exact CAS and return structured output.",
            model="qwen-local",
            temperature=0,
            max_tokens=256,
        )
        attempt, attempt_clock = start_search_attempt(
            db,
            search_run=run,
            agent_run=agent,
            connector="duckduckgo_html",
            query='"Aspirin" "50-78-2" manufacturer China',
            language="en",
            purpose="Find official product pages",
        )
        finish_search_attempt(attempt, attempt_clock, result_count=3)
        finish_agent_run(
            agent,
            agent_clock,
            output_payload={"queries": [attempt.query]},
        )
        finish_search_run(run)
        db.commit()
        return run.id


def test_owner_and_privileged_roles_can_read_full_trace(client):
    run_id = _build_completed_trace()

    for username in ("ivanov", "petrova", "admin", "auditor"):
        response = client.get(
            f"/search-runs/{run_id}", headers=_auth(client, username)
        )
        assert response.status_code == 200
        trace = response.json()
        assert trace["status"] == "completed"
        assert trace["owner_name"]
        assert trace["agent_runs"][0]["effective_system_prompt"]
        assert trace["agent_runs"][0]["output_payload"]["queries"]
        assert trace["search_attempts"][0]["connector"] == "duckduckgo_html"
        assert trace["search_attempts"][0]["result_count"] == 3


def test_buyer_cannot_read_another_users_trace(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "petrova").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "64-17-5", "name": "Ethanol"},
        )
        db.commit()
        run_id = run.id

    ivanov = _auth(client, "ivanov")
    assert client.get(f"/search-runs/{run_id}", headers=ivanov).status_code == 404
    listed_ids = {
        item["id"] for item in client.get("/search-runs", headers=ivanov).json()
    }
    assert run_id not in listed_ids


def test_search_run_requires_authentication(client):
    assert client.get("/search-runs").status_code == 401
