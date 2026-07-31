"""Persistent search traces are complete and respect project RBAC."""

import os
from datetime import timedelta
from uuid import UUID

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_runs.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, RfqAiSetting, SearchRun, User
from app.api.supplier_search import SearchRunCancelled
from app.search_worker import (
    process_next_job,
    process_ready_job,
    recover_interrupted_jobs,
)
from app.services.search_trace import (
    create_search_run,
    finish_agent_run,
    finish_search_attempt,
    finish_search_run,
    start_agent_run,
    start_search_attempt,
    utc_now,
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
            raw_output_payload={"results": [{"query": attempt.query}]},
            parsed_output_payload={"queries": [attempt.query]},
            validation_output_payload={"accepted": True},
            policy_output_payload={"queries": [attempt.query]},
        )
        finish_search_run(run)
        db.commit()
        return run.id


def _build_replayable_trace() -> tuple[int, str]:
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={
                "cas": "50-78-2",
                "name": "Aspirin",
                "country": "China",
            },
        )
        base_result = {
            "result_index": 0,
            "title": "Example Chemical",
            "url": "https://example.test/aspirin",
            "snippet": "Aspirin manufacturer",
            "company_name": "Example Chemical",
            "supplier_type": "manufacturer",
            "cas_status": "confirmed",
            "country_status": "claimed",
            "confidence": 85,
            "shortlist_eligible": True,
            "red_flags": [],
            "missing_evidence": [],
            "evidence": [
                {
                    "id": 101,
                    "source_document_id": 11,
                    "claim_type": "chemical_identity",
                    "claim_value": "CAS 50-78-2",
                    "support_status": "supports",
                    "quote": "Aspirin CAS 50-78-2",
                    "quote_verified": True,
                },
                {
                    "id": 102,
                    "source_document_id": 11,
                    "claim_type": "manufacturer_role",
                    "claim_value": "manufacturer",
                    "support_status": "supports",
                    "quote": "We manufacture aspirin",
                    "quote_verified": True,
                },
            ],
        }
        qualification, qualification_clock = start_agent_run(
            db,
            search_run=run,
            sequence=1,
            agent_slug="supplier_qualification",
            agent_name="Квалификация поставщика",
            execution_type="llm",
            input_payload={"chemical": {"cas": "50-78-2"}},
        )
        accepted_evidence = [
            {
                "result_index": 0,
                "claims": base_result["evidence"],
            }
        ]
        finish_agent_run(
            qualification,
            qualification_clock,
            output_payload={"qualified_results": [base_result]},
            raw_output_payload={"model_batches": []},
            parsed_output_payload={"results": []},
            validation_output_payload={
                "accepted_evidence": accepted_evidence,
                "rejected_evidence": [],
            },
            policy_output_payload={"qualified_results": [base_result]},
        )
        verification, verification_clock = start_agent_run(
            db,
            search_run=run,
            sequence=2,
            agent_slug="supplier_verifier",
            agent_name="Независимая проверка поставщика",
            execution_type="llm",
            input_payload={"candidates": [{"result_index": 0}]},
        )
        raw_verification = {
            "results": [
                {
                    "result_index": 0,
                    "substance_match": "exact",
                    "supplier_role": "manufacturer",
                    "verification_status": "confirmed",
                    "recommended_action": "shortlist",
                    "confidence": 90,
                    "reason": "Identity and manufacturing are supported.",
                    "supporting_claim_ids": [101, 102],
                    "contradictory_claim_ids": [],
                    "missing_evidence": [],
                }
            ]
        }
        finish_agent_run(
            verification,
            verification_clock,
            output_payload={"qualified_results": [base_result]},
            raw_output_payload={"model_batches": [raw_verification]},
            parsed_output_payload=raw_verification,
            validation_output_payload={"schema_rejections": []},
            policy_output_payload={"qualified_results": [base_result]},
        )
        run.result_payload = {
            "results": [
                {
                    "title": base_result["title"],
                    "url": base_result["url"],
                    "snippet": base_result["snippet"],
                }
            ]
        }
        finish_search_run(run)
        db.commit()
        return run.id, run.correlation_id


def test_owner_and_privileged_roles_can_read_full_trace(client):
    run_id = _build_completed_trace()

    for username in ("ivanov", "petrova", "admin", "auditor"):
        response = client.get(
            f"/search-runs/{run_id}", headers=_auth(client, username)
        )
        assert response.status_code == 200
        trace = response.json()
        assert trace["status"] == "completed"
        UUID(trace["correlation_id"])
        assert trace["graph_version"] == "supplier-search.v1"
        assert trace["owner_name"]
        assert trace["agent_runs"][0]["contract_version"] == "v1"
        assert trace["agent_runs"][0]["effective_system_prompt"]
        assert trace["agent_runs"][0]["output_payload"]["queries"]
        assert trace["agent_runs"][0]["raw_output_payload"]["results"]
        assert trace["agent_runs"][0]["parsed_output_payload"]["queries"]
        assert trace["agent_runs"][0]["validation_output_payload"] == {
            "accepted": True
        }
        assert trace["agent_runs"][0]["policy_output_payload"]["queries"]
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


def test_validator_replay_is_isolated_and_uses_no_network_or_llm(
    client, monkeypatch
):
    run_id, original_correlation_id = _build_replayable_trace()
    buyer = _auth(client, "ivanov")
    original_before = client.get(
        f"/search-runs/{run_id}",
        headers=buyer,
    ).json()

    def unexpected_call(*args, **kwargs):
        raise AssertionError("validator replay must not call external services")

    monkeypatch.setattr(
        "app.extraction.llm_client.LLMClient.generate_json",
        unexpected_call,
    )
    monkeypatch.setattr(
        "app.connectors.web_page.fetch_web_page",
        unexpected_call,
    )
    response = client.post(
        f"/search-runs/{run_id}/replay",
        headers=buyer,
        json={"mode": "validator"},
    )

    assert response.status_code == 201
    replay = response.json()
    assert replay["search_run_id"] != run_id
    assert replay["correlation_id"] != original_correlation_id
    assert replay["parent_search_run_id"] == run_id
    assert replay["replay_mode"] == "validator"
    assert replay["status"] == "completed"

    trace = client.get(
        f"/search-runs/{replay['search_run_id']}",
        headers=buyer,
    ).json()
    assert trace["parent_search_run_id"] == run_id
    assert trace["replay_mode"] == "validator"
    assert trace["search_attempts"] == []
    assert trace["source_documents"] == []
    assert len(trace["agent_runs"]) == 1
    stage = trace["agent_runs"][0]
    assert stage["execution_type"] == "validator_replay"
    assert stage["raw_output_payload"]["model_batches"]
    assert stage["parsed_output_payload"]["results"][0][
        "verification_status"
    ] == "confirmed"
    assert stage["validation_output_payload"]["schema_rejections"] == []
    assert stage["policy_output_payload"]["shortlist_count"] == 1
    assert trace["qualified_results"][0]["shortlist_eligible"] is True

    original_after = client.get(
        f"/search-runs/{run_id}",
        headers=buyer,
    ).json()
    assert original_after["correlation_id"] == original_correlation_id
    assert original_after["agent_runs"] == original_before["agent_runs"]
    assert original_after["parent_search_run_id"] is None
    assert original_after["replay_mode"] is None


def test_validator_replay_rejects_run_without_auditor_artifacts(client):
    run_id = _build_completed_trace()
    response = client.post(
        f"/search-runs/{run_id}/replay",
        headers=_auth(client, "ivanov"),
        json={"mode": "validator"},
    )
    assert response.status_code == 409


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
    payload = response.json()
    UUID(payload["correlation_id"])
    return payload


def test_unknown_agent_contract_requires_an_explicit_version(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "50-78-2", "name": "Aspirin"},
        )

        with pytest.raises(ValueError, match="Неизвестна версия контракта"):
            start_agent_run(
                db,
                search_run=run,
                sequence=1,
                agent_slug="future_agent",
                agent_name="Новый агент",
                execution_type="llm",
            )

        stage, _ = start_agent_run(
            db,
            search_run=run,
            sequence=1,
            agent_slug="future_agent",
            agent_name="Новый агент",
            execution_type="llm",
            contract_version="future-agent.v1",
        )
        assert stage.contract_version == "future-agent.v1"


def test_search_jobs_are_queued_and_processed_fifo(client):
    buyer = _auth(client, "ivanov")
    first = _enqueue_search(
        client, buyer, cas="50-78-2", name="Aspirin"
    )
    second = _enqueue_search(
        client, buyer, cas="64-17-5", name="Ethanol"
    )
    assert first["correlation_id"] != second["correlation_id"]
    assert first["status"] == "queued"
    assert first["queue_position"] == 1
    assert second["queue_position"] == 2

    listed = {
        item["id"]: item for item in client.get("/search-runs", headers=buyer).json()
    }
    assert (
        listed[first["search_run_id"]]["correlation_id"]
        == first["correlation_id"]
    )
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


def test_worker_leaves_queue_untouched_until_local_llm_is_ready(client):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="50-78-2", name="Aspirin"
    )
    processor_called = False

    def processor():
        nonlocal processor_called
        processor_called = True
        return queued["search_run_id"]

    ready, processed_id = process_ready_job(
        readiness_checker=lambda: (False, "model is loading"),
        processor=processor,
    )

    assert ready is False
    assert processed_id is None
    assert processor_called is False
    trace = client.get(
        f"/search-runs/{queued['search_run_id']}", headers=buyer
    ).json()
    assert trace["status"] == "queued"

    ready, processed_id = process_ready_job(
        readiness_checker=lambda: (True, None),
        processor=processor,
    )
    assert ready is True
    assert processed_id == queued["search_run_id"]
    assert processor_called is True

    with SessionLocal() as db:
        run = db.get(SearchRun, queued["search_run_id"])
        db.delete(run)
        db.commit()


def test_worker_automatically_continues_to_source_check_and_qualification(
    client, monkeypatch
):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="50-78-2", name="Aspirin"
    )
    source_url = "https://manufacturer.example/aspirin"

    def successful_executor(data, db, user, *, search_run):
        return {
            "search_run_id": search_run.id,
            "query": f'"{data.cas}" manufacturer',
            "queries_used": [f'"{data.cas}" manufacturer'],
            "results": [
                {
                    "title": "Aspirin manufacturer",
                    "url": source_url,
                    "snippet": "We manufacture Aspirin CAS 50-78-2.",
                    "country_hint": "likely",
                }
            ],
        }

    def successful_qualifier(data, db, user):
        assert data.search_run_id == queued["search_run_id"]
        assert data.results[0].url == source_url
        run = db.get(SearchRun, data.search_run_id)
        fetch_stage, fetch_clock = start_agent_run(
            db,
            search_run=run,
            sequence=5,
            agent_slug="source_fetch",
            agent_name="Загрузка первичных страниц",
            execution_type="tool",
            input_payload={"urls": [source_url]},
        )
        finish_agent_run(
            fetch_stage,
            fetch_clock,
            output_payload={"sources": [{"url": source_url, "status": "completed"}]},
        )
        qualification_stage, qualification_clock = start_agent_run(
            db,
            search_run=run,
            sequence=6,
            agent_slug="supplier_qualification",
            agent_name="Квалификация поставщиков",
            execution_type="llm",
            input_payload={"sources": [{"url": source_url}]},
        )
        finish_agent_run(
            qualification_stage,
            qualification_clock,
            output_payload={
                "qualified_results": [
                    {
                        "url": source_url,
                        "supplier_type": "manufacturer",
                    }
                ]
            },
        )
        finish_search_run(run)
        db.commit()
        return {"search_run_id": run.id, "results": []}

    monkeypatch.setattr(
        "app.search_worker.execute_supplier_search",
        successful_executor,
    )
    monkeypatch.setattr(
        "app.search_worker.execute_supplier_qualification",
        successful_qualifier,
    )
    processed_id = process_next_job()

    assert processed_id == queued["search_run_id"]
    trace = client.get(f"/search-runs/{processed_id}", headers=buyer).json()
    assert trace["status"] == "completed"
    assert trace["result_payload"]["results"][0]["url"] == source_url
    assert [stage["agent_slug"] for stage in trace["agent_runs"]] == [
        "source_fetch",
        "supplier_qualification",
    ]
    assert trace["summary"]["qualified_count"] == 1
    assert trace["summary"]["manufacturer_candidate_count"] == 1


def test_worker_resumes_linked_search_completed_run_without_repeating_search(
    client,
):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="50-78-2", name="Aspirin"
    )
    source_url = "https://manufacturer.example/resumed-aspirin"

    with SessionLocal() as db:
        run = db.get(SearchRun, queued["search_run_id"])
        assert run is not None
        rfq = RFQ(
            cas="50-78-2",
            name="Aspirin qualification resume",
            owner_id=run.owner_id,
        )
        db.add(rfq)
        db.flush()
        run.rfq_id = rfq.id
        run.status = "search_completed"
        run.result_payload = {
            "search_run_id": run.id,
            "query": '"50-78-2" manufacturer',
            "queries_used": ['"50-78-2" manufacturer'],
            "results": [
                {
                    "title": "Resumable Aspirin manufacturer",
                    "url": source_url,
                    "snippet": "We manufacture Aspirin CAS 50-78-2.",
                    "country_hint": "likely",
                }
            ],
        }
        db.commit()

    def repeated_search(*args, **kwargs):
        raise AssertionError("persisted candidates must be reused")

    def successful_qualifier(data, db, user):
        assert data.search_run_id == queued["search_run_id"]
        assert data.results[0].url == source_url
        run = db.get(SearchRun, data.search_run_id)
        fetch_stage, fetch_clock = start_agent_run(
            db,
            search_run=run,
            sequence=5,
            agent_slug="source_fetch",
            agent_name="Загрузка первичных страниц",
            execution_type="tool",
            input_payload={"urls": [source_url]},
        )
        finish_agent_run(
            fetch_stage,
            fetch_clock,
            output_payload={"sources": [{"url": source_url, "status": "completed"}]},
        )
        qualification_stage, qualification_clock = start_agent_run(
            db,
            search_run=run,
            sequence=6,
            agent_slug="supplier_qualification",
            agent_name="Квалификация поставщиков",
            execution_type="llm",
            input_payload={"sources": [{"url": source_url}]},
        )
        finish_agent_run(
            qualification_stage,
            qualification_clock,
            output_payload={
                "qualified_results": [
                    {
                        "url": source_url,
                        "supplier_type": "manufacturer",
                    }
                ]
            },
        )
        finish_search_run(run)
        db.commit()
        return {"search_run_id": run.id, "results": []}

    processed_id = process_next_job(
        executor=repeated_search,
        qualifier=successful_qualifier,
    )

    assert processed_id == queued["search_run_id"]
    trace = client.get(f"/search-runs/{processed_id}", headers=buyer).json()
    assert trace["status"] == "completed"
    assert trace["result_payload"]["results"][0]["url"] == source_url
    assert [stage["agent_slug"] for stage in trace["agent_runs"]] == [
        "source_fetch",
        "supplier_qualification",
    ]


def test_worker_completes_full_job_when_search_finds_no_candidates(
    client, monkeypatch
):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="64-17-5", name="Ethanol"
    )

    monkeypatch.setattr(
        "app.search_worker.execute_supplier_search",
        lambda data, db, user, *, search_run: {
            "search_run_id": search_run.id,
            "query": f'"{data.cas}" manufacturer',
            "queries_used": [f'"{data.cas}" manufacturer'],
            "results": [],
        },
    )

    assert process_next_job() == queued["search_run_id"]
    trace = client.get(
        f"/search-runs/{queued['search_run_id']}", headers=buyer
    ).json()
    assert trace["status"] == "completed"
    assert trace["summary"]["candidate_count"] == 0
    assert trace["summary"]["qualification_status"] == "not_started"


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


def test_supplier_search_rejects_unsupported_country(client):
    buyer = _auth(client, "ivanov")
    response = client.post(
        "/supplier-search/jobs",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "country": "Германия",
        },
    )

    assert response.status_code == 422
    assert "Россия, Китай, Индия" in response.text


def test_rfq_normalizes_allowed_country_aliases_and_rejects_other_markets(
    client,
):
    buyer = _auth(client, "ivanov")
    accepted = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "incoterms": ["CIP"],
            "search_countries": ["Russia", "China", "India"],
        },
    )
    rejected = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "incoterms": ["CIP"],
            "search_countries": ["Турция"],
        },
    )

    assert accepted.status_code == 201
    assert accepted.json()["search_countries"] == ["Россия", "Китай", "Индия"]
    assert rejected.status_code == 422
    assert "Россия, Китай, Индия" in rejected.text


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
    assert response.json()["detail"] == "Запрос не найден"


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


def test_stale_search_can_be_restarted_without_losing_its_trace(client):
    buyer = _auth(client, "ivanov")
    rfq = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
    ).json()
    queued = client.post(
        f"/supplier-search/jobs?rfq_id={rfq['id']}",
        headers=buyer,
        json={
            "cas": rfq["cas"],
            "name": rfq["name"],
            "country": "Китай",
        },
    ).json()

    with SessionLocal() as db:
        run = db.get(SearchRun, queued["search_run_id"])
        run.started_at = utc_now() - timedelta(minutes=31)
        db.commit()

    stale = client.get(
        f"/search-runs/{queued['search_run_id']}", headers=buyer
    ).json()
    assert stale["is_stale"] is True
    assert stale["can_restart"] is True

    response = client.post(
        f"/search-runs/{queued['search_run_id']}/restart",
        headers=buyer,
    )

    assert response.status_code == 202
    restarted_payload = response.json()
    restarted_id = restarted_payload["search_run_id"]
    assert restarted_id != queued["search_run_id"]
    UUID(restarted_payload["correlation_id"])
    assert restarted_payload["correlation_id"] != queued["correlation_id"]
    with SessionLocal() as db:
        old_run = db.get(SearchRun, queued["search_run_id"])
        restarted = db.get(SearchRun, restarted_id)
        assert old_run.status == "cancelled"
        assert old_run.completed_at is not None
        assert restarted.status == "queued"
        assert restarted.correlation_id == restarted_payload["correlation_id"]
        assert restarted.graph_version == "supplier-search.v1"
        assert (
            restarted.input_payload["restart_of_search_run_id"]
            == queued["search_run_id"]
        )


def test_active_search_cannot_be_restarted_and_auditor_cannot_restart(client):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(
        client, buyer, cas="50-78-2", name="Active aspirin search"
    )

    active_response = client.post(
        f"/search-runs/{queued['search_run_id']}/restart",
        headers=buyer,
    )
    auditor_response = client.post(
        f"/search-runs/{queued['search_run_id']}/restart",
        headers=_auth(client, "auditor"),
    )

    assert active_response.status_code == 409
    assert auditor_response.status_code == 403


def test_repeated_country_search_excludes_and_merges_previous_suppliers(client):
    buyer = _auth(client, "ivanov")
    rfq = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={"cas": "64-17-5", "name": "Ethanol", "incoterms": ["CIP"]},
    ).json()
    first = client.post(
        f"/supplier-search/jobs?rfq_id={rfq['id']}",
        headers=buyer,
        json={
            "cas": rfq["cas"],
            "name": rfq["name"],
            "country": "Индия",
        },
    ).json()
    with SessionLocal() as db:
        run = db.get(SearchRun, first["search_run_id"])
        run.result_payload = {
            "search_run_id": run.id,
            "results": [
                {
                    "title": "Existing Supplier",
                    "url": "https://existing.example/ethanol",
                    "snippet": "Ethanol manufacturer",
                    "country_hint": "likely",
                }
            ],
        }
        stage, clock = start_agent_run(
            db,
            search_run=run,
            sequence=6,
            agent_slug="supplier_qualification",
            agent_name="Оценка поставщиков",
            execution_type="deterministic",
        )
        finish_agent_run(
            stage,
            clock,
            output_payload={
                "qualified_results": [
                    {
                        "company_name": "Existing Chemicals Ltd",
                        "url": "https://existing.example/ethanol",
                        "supplier_type": "manufacturer",
                    }
                ]
            },
        )
        finish_search_run(run)
        db.commit()

    second_response = client.post(
        f"/supplier-search/jobs?rfq_id={rfq['id']}",
        headers=buyer,
        json={
            "cas": rfq["cas"],
            "name": rfq["name"],
            "country": "Индия",
        },
    )
    assert second_response.status_code == 202
    second_id = second_response.json()["search_run_id"]
    with SessionLocal() as db:
        second = db.get(SearchRun, second_id)
        assert second.input_payload["excluded_supplier_domains"] == [
            "existing.example"
        ]
        assert "existing chemicals ltd" in second.input_payload[
            "excluded_supplier_names"
        ]
        second.result_payload = {
            "search_run_id": second.id,
            "results": [
                {
                    "title": "Existing Supplier duplicate",
                    "url": "https://existing.example/another-page",
                    "snippet": "duplicate",
                    "country_hint": "likely",
                },
                {
                    "title": "New Supplier",
                    "url": "https://new.example/ethanol",
                    "snippet": "new result",
                    "country_hint": "likely",
                },
            ],
        }
        finish_search_run(second)
        db.commit()

    merged = client.get(
        f"/search-runs/{second_id}?merge_country=true",
        headers=buyer,
    ).json()
    assert merged["merged_run_count"] == 2
    assert {
        item["url"].split("/")[2] for item in merged["candidate_results"]
    } == {"existing.example", "new.example"}


def test_worker_does_not_resurrect_a_cancelled_search(client):
    buyer = _auth(client, "ivanov")
    _enqueue_search(
        client, buyer, cas="50-78-2", name="Cancellation race"
    )

    def cancelled_executor(data, db, user, *, search_run):
        search_run.status = "cancelled"
        search_run.error = "Перезапущено пользователем"
        search_run.completed_at = utc_now()
        db.commit()
        raise SearchRunCancelled("cancelled during an external call")

    processed_id = process_next_job(executor=cancelled_executor)

    assert processed_id is not None
    with SessionLocal() as db:
        processed = db.get(SearchRun, processed_id)
        assert processed.status == "cancelled"
        assert processed.error == "Перезапущено пользователем"


def _cancel_other_queued_runs(run_id: int) -> None:
    """Изолирует worker-тест от задач, оставшихся в очереди другими тестами."""
    with SessionLocal() as db:
        others = db.query(SearchRun).filter(
            SearchRun.mode == "queued_search",
            SearchRun.id != run_id,
            SearchRun.status.in_(["queued", "search_completed"]),
        )
        for other in others:
            other.status = "cancelled"
        db.commit()


def _fail_run_with_saved_results(run_id: int, *, with_results: bool = True) -> None:
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run is not None
        rfq = RFQ(
            cas="50-78-2",
            name="Resume after failure",
            owner_id=run.owner_id,
        )
        db.add(rfq)
        db.flush()
        run.rfq_id = rfq.id
        run.status = "failed"
        run.error = "Локальная ИИ-модель недоступна."
        run.completed_at = utc_now()
        if with_results:
            run.result_payload = {
                "search_run_id": run.id,
                "query": '"50-78-2" manufacturer',
                "queries_used": ['"50-78-2" manufacturer'],
                "results": [
                    {
                        "title": "Aspirin manufacturer",
                        "url": "https://manufacturer.example/aspirin",
                        "snippet": "We manufacture Aspirin CAS 50-78-2.",
                        "country_hint": "likely",
                    }
                ],
            }
        db.commit()


def test_resume_returns_failed_run_to_queue_without_new_run(client):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(client, buyer, cas="50-78-2", name="Aspirin")
    run_id = queued["search_run_id"]
    _fail_run_with_saved_results(run_id)

    listed = client.get("/search-runs", headers=buyer).json()
    listed_run = next(item for item in listed if item["id"] == run_id)
    assert listed_run["can_resume"] is True

    response = client.post(f"/search-runs/{run_id}/resume", headers=buyer)
    assert response.status_code == 202
    payload = response.json()
    assert payload["search_run_id"] == run_id
    assert payload["correlation_id"] == queued["correlation_id"]
    assert payload["status"] == "search_completed"

    trace = client.get(f"/search-runs/{run_id}", headers=buyer).json()
    assert trace["status"] == "search_completed"
    assert trace["error"] is None
    assert trace["input_payload"]["resume_count"] == 1


def test_resume_requires_saved_search_results(client):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(client, buyer, cas="50-78-2", name="Aspirin")
    run_id = queued["search_run_id"]
    _fail_run_with_saved_results(run_id, with_results=False)

    response = client.post(f"/search-runs/{run_id}/resume", headers=buyer)
    assert response.status_code == 409

    listed = client.get("/search-runs", headers=buyer).json()
    listed_run = next(item for item in listed if item["id"] == run_id)
    assert listed_run["can_resume"] is False
    assert listed_run["can_restart"] is True


def test_worker_requeues_job_when_llm_is_unavailable(client, monkeypatch):
    from fastapi import HTTPException

    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(client, buyer, cas="50-78-2", name="Aspirin")
    run_id = queued["search_run_id"]
    _cancel_other_queued_runs(run_id)

    def llm_down(*args, **kwargs):
        raise HTTPException(
            status_code=503,
            detail={"message": "Локальная ИИ-модель недоступна."},
        )

    monkeypatch.setattr(
        "app.search_worker.execute_supplier_search", llm_down
    )

    # Три временных сбоя возвращают задачу в очередь без статуса ошибки.
    for attempt in (1, 2, 3):
        assert process_next_job() == run_id
        with SessionLocal() as db:
            run = db.get(SearchRun, run_id)
            assert run.status == "queued"
            assert run.error is None
            assert run.input_payload["llm_auto_retry_count"] == attempt
            assert "недоступна" in run.input_payload["last_llm_outage"]

    # Исчерпание лимита повторов делает ошибку постоянной.
    assert process_next_job() == run_id
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run.status == "failed"
        assert run.input_payload["llm_auto_retry_count"] == 3


def test_worker_logic_error_is_not_requeued(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    queued = _enqueue_search(client, buyer, cas="50-78-2", name="Aspirin")
    run_id = queued["search_run_id"]
    _cancel_other_queued_runs(run_id)

    def broken_executor(*args, **kwargs):
        raise ValueError("логическая ошибка конвейера")

    monkeypatch.setattr(
        "app.search_worker.execute_supplier_search", broken_executor
    )
    assert process_next_job() == run_id
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run.status == "failed"
        assert "llm_auto_retry_count" not in (run.input_payload or {})
