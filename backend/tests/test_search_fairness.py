"""Five substances share workers instead of queueing all countries in FIFO order.

Set SEARCH_TEST_POSTGRES_DSN for the real PostgreSQL concurrency check. All
tables and synthetic data live in a temporary schema removed after each test.
No external search, model, or communication calls are made.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_fairness_app.db")

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import search_worker
from app.models import Base, RFQ, SearchRun, User
from app.services.search_lease import grant_lease
from app.services.search_trace import utc_now


@pytest.fixture
def sessions(tmp_path):
    dsn = os.environ.get("SEARCH_TEST_POSTGRES_DSN")
    schema = "test_search_fairness_" + uuid4().hex
    admin = None
    if dsn:
        admin = create_engine(dsn)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(dsn, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with factory() as db:
            db.add(User(id=1, username="synthetic", full_name="Synthetic", password_hash="unused"))
            db.commit()
        yield factory
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def enqueue(sessions, cas="50-78-2", *, rfq_id=None, status="queued"):
    with sessions() as db:
        run = SearchRun(
            owner_id=1, rfq_id=rfq_id, mode="queued_search", status=status,
            input_payload={"cas": cas, "name": "Synthetic substance", "country": "Китай"},
            started_at=utc_now(),
        )
        db.add(run)
        db.commit()
        return run.id


def claim(sessions, owner="worker"):
    with sessions() as db:
        result = search_worker.claim_next_job(db, owner)
        return result[0] if result else None


def test_first_five_claims_cover_five_substances(sessions):
    # Deliberately enqueue all three countries for A before B, etc.
    groups = [[enqueue(sessions, f"{50 + i}-00-0") for _ in range(3)] for i in range(5)]
    claimed = [claim(sessions, f"worker-{i}") for i in range(5)]
    assert claimed == [group[0] for group in groups]
    # Spare capacity can still work on another country of an active substance.
    assert claim(sessions, "spare-worker") == groups[0][1]


def test_one_substance_can_use_all_available_workers(sessions):
    ids = [enqueue(sessions) for _ in range(5)]
    assert [claim(sessions, f"worker-{i}") for i in range(5)] == ids
    assert claim(sessions, "extra-worker") is None


@pytest.mark.parametrize("inactive", ["expired", "failed", "cancelled", "completed"])
def test_expired_or_terminal_work_does_not_penalize_substance(sessions, inactive):
    active_id = enqueue(sessions, status="identifying")
    with sessions() as db:
        run = db.get(SearchRun, active_id)
        grant_lease(run, "old-worker")
        if inactive == "expired":
            run.lease_expires_at = utc_now() - timedelta(seconds=1)
        else:
            run.status = inactive
        db.commit()
    first = enqueue(sessions)
    enqueue(sessions, "64-17-5")
    assert claim(sessions) == first


def test_no_cas_groups_only_by_rfq_not_by_name(sessions):
    with sessions() as db:
        db.add_all([RFQ(id=1, name="Same name"), RFQ(id=2, name="Same name")])
        db.commit()
    first = enqueue(sessions, None, rfq_id=1)
    second_country = enqueue(sessions, "", rfq_id=1)
    other_rfq = enqueue(sessions, None, rfq_id=2)
    assert claim(sessions, "worker-1") == first
    assert claim(sessions, "worker-2") == other_rfq
    assert claim(sessions, "worker-3") == second_country


def test_five_workers_execute_different_substances_together(sessions, monkeypatch):
    groups = [[enqueue(sessions, cas) for _ in range(3)] for cas in
              ["50-78-2", "64-17-5", "67-56-1", "67-64-1", "7732-18-5"]]
    first_wave = threading.Barrier(5, timeout=15)
    observed = []
    observation_lock = threading.Lock()
    sqlite_claim_lock = threading.Lock()
    original_claim = search_worker.claim_next_job

    def locked_claim(db, owner=None):
        # SQLite lacks row/advisory locks. PostgreSQL exercises the real
        # cross-connection lock, with no test-side serialization.
        guard = sqlite_claim_lock if db.bind.dialect.name == "sqlite" else nullcontext()
        with guard:
            return original_claim(db, owner)

    monkeypatch.setattr(search_worker, "claim_next_job", locked_claim)

    def execute(data, db, user, *, search_run):
        with observation_lock:
            observed.append((search_run.id, data.cas))
            wave_index = len(observed)
        if wave_index <= 5:
            first_wave.wait()  # No first job finishes before all five start.
        search_run.status = "completed"
        search_run.completed_at = utc_now()
        return {"search_run_id": search_run.id, "results": []}

    def worker():
        while search_worker.process_next_job(session_factory=sessions, executor=execute) is not None:
            pass

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker) for _ in range(5)]
        for future in futures:
            future.result(timeout=40)

    assert len({cas for _, cas in observed[:5]}) == 5
    expected = {run_id for group in groups for run_id in group}
    assert len(observed) == len(expected) == 15
    assert {run_id for run_id, _ in observed} == expected
    with sessions() as db:
        runs = db.scalars(select(SearchRun)).all()
        assert all(run.status == "completed" and run.lease_owner is None for run in runs)
        assert all(run.lease_generation == 1 for run in runs)
    print("Five distinct substances overlapped; all 15 country jobs completed exactly once.")
