"""Persistent search traces are complete and respect project RBAC."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_runs.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RfqAiSetting, SearchRun, User
from app.search_worker import process_next_job, recover_interrupted_jobs
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
        assert trace["search_attempts"][0]["results_payload"] is None
        assert trace["summary"] == {
            "planned_query_count": 1,
            "executed_query_count": 1,
            "raw_page_count": 3,
            "candidate_count": 0,
            "qualified_count": 0,
            "manufacturer_candidate_count": 0,
            "qualification_status": "not_started",
        }


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


def _enqueue_search(
    client: TestClient,
    headers: dict[str, str],
    *,
    cas: str,
    name: str,
) -> dict:
    response = client.post(
        "/supplier-search/jobs",
        headers=headers,
        json={"cas": cas, "name": name, "country": "China"},
    )
    assert response.status_code == 202
    return response.json()


def test_search_jobs_are_queued_and_processed_fifo(client):
    buyer = _auth(client, "ivanov")
    first = _enqueue_search(
        client, buyer, cas="50-78-2", name="Aspirin"
    )
    second = _enqueue_search(
        client, buyer, cas="64-17-5", name="Ethanol"
    )
    assert first["status"] == "queued"
    assert first["queue_position"] == 1
    assert second["queue_position"] == 2

    listed = {
        item["id"]: item for item in client.get("/search-runs", headers=buyer).json()
    }
    assert listed[first["search_run_id"]]["queue_position"] == 1
    assert listed[second["search_run_id"]]["queue_position"] == 2

    def successful_executor(data, db, user, *, search_run):
        return {
            "search_run_id": search_run.id,
            "query": f'"{data.cas}" manufacturer',
            "queries_used": [f'"{data.cas}" manufacturer'],
            "results": [],
        }

    processed_id = process_next_job(executor=successful_executor)
    assert processed_id == first["search_run_id"]
    first_trace = client.get(
        f"/search-runs/{processed_id}", headers=buyer
    ).json()
    assert first_trace["status"] == "search_completed"
    assert first_trace["result_payload"]["search_run_id"] == processed_id

    second_trace = client.get(
        f"/search-runs/{second['search_run_id']}", headers=buyer
    ).json()
    assert second_trace["status"] == "queued"
    assert second_trace["queue_position"] == 1
    assert process_next_job(executor=successful_executor) == second["search_run_id"]


def test_failed_job_does_not_block_the_queue(client):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="67-56-1", name="Methanol"
    )

    def failed_executor(*args, **kwargs):
        raise RuntimeError("search connector unavailable")

    processed_id = process_next_job(executor=failed_executor)
    assert processed_id == queued["search_run_id"]
    trace = client.get(f"/search-runs/{processed_id}", headers=buyer).json()
    assert trace["status"] == "failed"
    assert trace["error"] == "search connector unavailable"


def test_worker_marks_interrupted_jobs_as_failed(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "71-43-2", "name": "Benzene"},
            mode="queued_search",
            status="searching",
        )
        db.commit()
        run_id = run.id

    with SessionLocal() as db:
        assert recover_interrupted_jobs(db) == 1
        recovered = db.get(SearchRun, run_id)
        assert recovered is not None
        assert recovered.status == "failed"
        assert "перезапуском worker" in recovered.error


def test_invalid_cas_is_not_enqueued(client):
    buyer = _auth(client, "ivanov")
    response = client.post(
        "/supplier-search/jobs",
        headers=buyer,
        json={"cas": "50-78-3", "name": "Aspirin", "country": "China"},
    )
    assert response.status_code == 422


def test_search_job_is_bound_to_rfq_and_uses_its_substance(client):
    buyer = _auth(client, "ivanov")
    rfq = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
    ).json()

    response = client.post(
        f"/supplier-search/jobs?rfq_id={rfq['id']}",
        headers=buyer,
        json={
            "cas": "64-17-5",
            "name": "Ethanol",
            "country": "China",
        },
    )

    assert response.status_code == 202
    run_id = response.json()["search_run_id"]
    trace = client.get(f"/search-runs/{run_id}", headers=buyer).json()
    assert trace["rfq_id"] == rfq["id"]
    assert trace["input_payload"]["cas"] == rfq["cas"]
    assert trace["input_payload"]["name"] == rfq["name"]

    listed = client.get(
        f"/search-runs?rfq_id={rfq['id']}", headers=buyer
    ).json()
    assert [item["id"] for item in listed] == [run_id]


def test_buyer_cannot_enqueue_search_for_another_users_rfq(client):
    head = _auth(client, "petrova")
    rfq = client.post(
        "/rfq?verify=false",
        headers=head,
        json={"cas": "64-17-5", "name": "Ethanol", "incoterms": ["CIP"]},
    ).json()
    buyer = _auth(client, "ivanov")

    response = client.post(
        f"/supplier-search/jobs?rfq_id={rfq['id']}",
        headers=buyer,
        json={"cas": rfq["cas"], "name": rfq["name"], "country": "China"},
    )

    assert response.status_code == 404


def test_legacy_search_results_are_visible_without_result_payload(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "50-78-2", "name": "Aspirin"},
        )
        stage, clock = start_agent_run(
            db,
            search_run=run,
            sequence=1,
            agent_slug="web_search",
            agent_name="Поиск в открытых источниках",
            execution_type="tool",
            input_payload={"queries": [{"query": "legacy query"}]},
        )
        finish_agent_run(
            stage,
            clock,
            output_payload={
                "results": [
                    {
                        "title": "Legacy candidate",
                        "url": "https://legacy.example/product",
                        "snippet": "Aspirin CAS 50-78-2",
                        "country_hint": "likely",
                    }
                ]
            },
        )
        finish_search_run(run)
        db.commit()
        run_id = run.id

    trace = client.get(
        f"/search-runs/{run_id}", headers=_auth(client, "ivanov")
    ).json()
    assert trace["result_payload"] is None
    assert trace["result_count"] == 1
    assert trace["summary"]["candidate_count"] == 1
    assert trace["candidate_results"][0]["title"] == "Legacy candidate"


def test_creating_request_can_start_search_for_each_selected_country(client):
    buyer = _auth(client, "ivanov")
    response = client.post(
        "/rfq?verify=false&start_search=true",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "incoterms": ["CIP"],
            "search_countries": ["Китай", "Индия", " китай "],
            "supplier_target": 7,
            "additional_instructions": "Только производители с GMP",
        },
    )

    assert response.status_code == 201
    request = response.json()
    assert request["search_countries"] == ["Китай", "Индия"]
    assert request["supplier_target"] == 7

    runs = client.get(
        f"/search-runs?rfq_id={request['id']}",
        headers=buyer,
    ).json()
    assert [run["input_payload"]["country"] for run in reversed(runs)] == [
        "Китай",
        "Индия",
    ]
    assert all(run["status"] == "queued" for run in runs)
    assert all(run["rfq_id"] == request["id"] for run in runs)
    assert all(run["input_payload"]["limit"] == 7 for run in runs)
    assert all(
        run["input_payload"]["additional_instructions"]
        == "Только производители с GMP"
        for run in runs
    )

    with SessionLocal() as db:
        setting = db.get(RfqAiSetting, request["id"])
        assert setting is not None
        assert setting.additional_instructions == "Только производители с GMP"
