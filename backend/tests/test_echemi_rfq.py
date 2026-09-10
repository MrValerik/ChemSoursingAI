"""RFQ integration enqueues durable work; no live services are contacted."""
from datetime import datetime, timezone
from sqlalchemy import select
from app.models import RFQ, SearchRun
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole
from app.services.search_trace import create_search_run
from app.services.echemi_rfq import ensure_rfq_search
from app.api.echemi_manual import allowed
from tests.test_echemi_search import env


def rfq(db, **kwargs):
    row = RFQ(name="Aspirin", cas="50-78-2", owner_id=42, incoterms=["FCA"])
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.add(row); db.flush()
    return row


def enqueue(db, row, country="Китай"):
    return create_search_run(db, owner_id=42, rfq_id=row.id,
        input_payload={"cas": row.cas, "name": row.name, "country": country},
        mode="queued_search", status="queued")


def test_country_jobs_share_browser_job_and_retry_preserves_history(env):
    client, _, sessions = env
    with sessions() as db:
        row = rfq(db)
        first = enqueue(db, row)
        enqueue(db, row, "Индия")
        db.commit()
        jobs = db.scalars(select(EchemiSearch)).all()
        assert len(jobs) == 1 and jobs[0].query == "50-78-2"
        assert jobs[0].rfq_id == row.id and jobs[0].status == "queued"
        rid, sid = row.id, jobs[0].id
        assert first.status == "queued"
        jobs[0].status = "blocked"
        jobs[0].results = [{"title": "Preserved evidence"}]
        db.commit()
        enqueue(db, row)
        db.commit()
        assert len(db.scalars(select(EchemiSearch)).all()) == 2
        assert db.get(EchemiSearch, sid).results[0]["title"] == "Preserved evidence"
    response = client.get(f"/echemi-searches?rfq_id={rid}")
    assert response.status_code == 200 and len(response.json()) == 2
    active = response.json()[0]["id"]
    # A double click reuses unfinished work.
    assert client.post(f"/echemi-searches/rfq/{rid}").json()["id"] == active
    assert client.post(f"/echemi-searches/rfq/{rid}").json()["id"] == active


def test_owner_access_follows_rfq_reassignment_and_deletion(env):
    client, user, sessions = env
    with sessions() as db:
        row = rfq(db)
        enqueue(db, row); db.commit()
        job = db.scalar(select(EchemiSearch))
        job.status = "running"
        row.owner_id = 43
        db.commit()
        rid, sid = row.id, job.id
        assert not allowed(db, sid, user)
    assert client.get(f"/echemi-searches/{sid}").status_code == 404
    assert client.get(f"/echemi-searches?rfq_id={rid}").status_code == 404
    assert client.post(f"/echemi-searches/rfq/{rid}").status_code == 404
    user.id = 43
    assert client.get(f"/echemi-searches/{sid}").status_code == 200
    with sessions() as db:
        assert allowed(db, sid, user)
    user.role = UserRole.AUDITOR
    assert client.get(f"/echemi-searches/{sid}").status_code == 200
    assert client.post(f"/echemi-searches/rfq/{rid}").status_code == 403
    with sessions() as db:
        assert not allowed(db, sid, user)
        db.get(RFQ, rid).deleted_at = datetime.now(timezone.utc)
        db.commit()
    assert client.get(f"/echemi-searches/{sid}").status_code == 404


def test_name_fallback_analog_guard_and_transaction_rollback(env):
    client, _, sessions = env
    with sessions() as db:
        row = rfq(db, cas=None, name="Synthetic test blend")
        enqueue(db, row)
        assert db.scalar(select(EchemiSearch)).query == row.name
        db.rollback()
        assert db.scalar(select(EchemiSearch)) is None
        assert db.scalar(select(SearchRun)) is None
        analog = rfq(db, identification_method="analog")
        enqueue(db, analog); db.commit()
        assert db.scalar(select(EchemiSearch)) is None
        aid = analog.id
    assert client.post(f"/echemi-searches/rfq/{aid}").status_code == 422


def test_invalid_name_does_not_block_regular_search_or_get_truncated_and_sent(env):
    _, _, sessions = env
    with sessions() as db:
        row = rfq(db, cas=None, name="n" * 201)
        run = enqueue(db, row); db.commit()
        assert run.status == "queued"
        job = db.scalar(select(EchemiSearch))
        assert job.status == "failed" and job.finished_at


def test_different_rfqs_have_independent_results_and_unlinked_search_is_unchanged(env):
    client, _, sessions = env
    with sessions() as db:
        a, b = rfq(db), rfq(db)
        enqueue(db, a); enqueue(db, b); db.commit()
        assert len(db.scalars(select(EchemiSearch)).all()) == 2
        rid = a.id
    standalone = client.post("/echemi-searches", json={"query": "50-78-2"}).json()
    assert standalone["rfq_id"] is None
    assert len(client.get(f"/echemi-searches?rfq_id={rid}").json()) == 1


def test_actual_supplier_jobs_api_queues_echemi_with_authoritative_rfq_identity(env):
    from app.api.supplier_search import router
    client, _, sessions = env
    client.app.include_router(router)
    with sessions() as db:
        row = rfq(db)
        db.commit(); rid = row.id
    response = client.post(f"/supplier-search/jobs?rfq_id={rid}",
        json={"cas": "50-99-7", "name": "Wrong client value", "country": "Китай"})
    assert response.status_code == 202, response.text
    with sessions() as db:
        job = db.scalar(select(EchemiSearch))
        assert job.query == "50-78-2" and job.rfq_id == rid
