"""Аренда задач очереди: безопасность нескольких worker-процессов.

Проверяются четыре свойства, без которых нельзя включать реплики:

1. рестарт worker не трогает задачу, которую прямо сейчас ведёт другой;
2. брошенная задача возвращается в работу после истечения аренды;
3. зависший worker не перезаписывает результат нового владельца;
4. heartbeat продлевает аренду на время длинных этапов.
"""

import os
import threading
from datetime import timedelta
from time import sleep

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_lease.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import SearchRun, User
from app.search_worker import (
    claim_next_job,
    process_next_job,
    recover_interrupted_jobs,
    sweep_expired_leases,
)
from app.services.search_lease import (
    LeaseHeartbeat,
    LeaseLost,
    grant_lease,
    holds_lease,
    lease_is_recoverable,
    renew_lease,
    require_lease,
    worker_identity,
)
from app.services.search_trace import create_search_run, utc_now


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_search_lease.db"):
        os.remove("test_search_lease.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_search_lease.db"):
        os.remove("test_search_lease.db")


def _queued_run(status: str = "queued") -> int:
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "50-78-2", "name": "Aspirin"},
            mode="queued_search",
            status=status,
        )
        db.commit()
        return run.id


def _naive(value):
    """SQLite не хранит смещение, поэтому сравниваем в одном представлении.

    Продуктовый код это уже учитывает: `lease_is_recoverable` нормализует
    значение, а сравнение аренды в `claim_next_job` выполняется на стороне БД
    в одинаковом формате.
    """
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _delete(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        if run is not None:
            db.delete(run)
            db.commit()


def test_worker_identity_is_unique_per_process(client):
    assert worker_identity() != worker_identity()


def test_claim_grants_a_lease_with_growing_generation(client):
    run_id = _queued_run()
    worker = "worker-a"
    with SessionLocal() as db:
        claimed = claim_next_job(db, worker)
    assert claimed is not None
    claimed_id, generation = claimed
    assert claimed_id == run_id
    assert generation == 1

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run.lease_owner == worker
        assert run.lease_expires_at is not None
        assert holds_lease(run, worker, generation)
        assert not holds_lease(run, "worker-b", generation)
        # Перевыдача поднимает поколение: старый владелец становится неактуален.
        second = grant_lease(run, "worker-b")
        db.commit()
        assert second == 2
        assert not holds_lease(run, worker, generation)
    _delete(run_id)


def test_live_lease_of_another_worker_is_not_claimable(client):
    run_id = _queued_run()
    with SessionLocal() as db:
        assert claim_next_job(db, "worker-a") is not None

    # Задача снова стала queued (например, после возврата в очередь), но
    # аренда worker-a ещё жива.
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.status = "queued"
        db.commit()

    with SessionLocal() as db:
        assert claim_next_job(db, "worker-b") is None, (
            "задачу с живой чужой арендой нельзя забирать"
        )

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()

    with SessionLocal() as db:
        claimed = claim_next_job(db, "worker-b")
    assert claimed is not None, "просроченная аренда должна освобождать задачу"
    assert claimed[1] == 2
    _delete(run_id)


def test_restart_does_not_kill_a_job_running_on_another_worker(client):
    """Блокер масштабирования: рестарт одного worker убивал чужие задачи."""
    foreign_id = _queued_run()
    own_id = _queued_run()
    with SessionLocal() as db:
        foreign = db.get(SearchRun, foreign_id)
        foreign.status = "fetching_sources"
        grant_lease(foreign, "worker-remote")
        own = db.get(SearchRun, own_id)
        own.status = "identifying"
        grant_lease(own, "worker-local")
        db.commit()

    with SessionLocal() as db:
        recovered = recover_interrupted_jobs(db, "worker-local")

    with SessionLocal() as db:
        foreign = db.get(SearchRun, foreign_id)
        own = db.get(SearchRun, own_id)
        assert recovered == 1, "восстановлена должна быть только своя задача"
        assert foreign.status == "fetching_sources", (
            "чужая выполняющаяся задача не должна помечаться failed"
        )
        assert foreign.lease_owner == "worker-remote"
        assert own.status == "failed"
        assert own.lease_owner is None

    _delete(foreign_id)
    _delete(own_id)


def test_expired_foreign_lease_is_recoverable(client):
    run_id = _queued_run()
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.status = "fetching_sources"
        grant_lease(run, "worker-remote")
        run.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
        assert lease_is_recoverable(run, "worker-local")

    with SessionLocal() as db:
        assert recover_interrupted_jobs(db, "worker-local") == 1

    with SessionLocal() as db:
        assert db.get(SearchRun, run_id).status == "failed"
    _delete(run_id)


def test_lease_expiring_after_startup_is_swept(client):
    """Задача становится брошенной уже после старта остальных worker.

    Стартового восстановления для этого мало: в момент запуска аренда была
    ещё жива. Без периодического обхода такой запуск завис бы навсегда — он
    не терминальный, но и `claim_next_job` его не берёт, потому что статус не
    `queued` и не `search_completed`.
    """
    run_id = _queued_run()
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.status = "identifying"
        grant_lease(run, "worker-crashed")
        db.commit()

    # Момент старта другого worker: аренда ещё жива, задачу трогать нельзя.
    assert sweep_expired_leases(SessionLocal) == 0
    with SessionLocal() as db:
        assert db.get(SearchRun, run_id).status == "identifying"
        assert claim_next_job(db, "worker-local") is None

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()

    assert sweep_expired_leases(SessionLocal) == 1
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run.status == "failed"
        assert run.lease_owner is None
    _delete(run_id)


def test_renew_lease_fails_after_reassignment(client):
    run_id = _queued_run()
    with SessionLocal() as db:
        claimed = claim_next_job(db, "worker-a")
    assert claimed is not None
    _, generation = claimed

    with SessionLocal() as db:
        assert renew_lease(db, run_id, "worker-a", generation) is True

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        grant_lease(run, "worker-b")
        db.commit()

    with SessionLocal() as db:
        assert renew_lease(db, run_id, "worker-a", generation) is False
        run = db.get(SearchRun, run_id)
        with pytest.raises(LeaseLost):
            require_lease(run, "worker-a", generation)
    _delete(run_id)


def test_heartbeat_extends_the_lease_during_long_stages(client):
    run_id = _queued_run()
    with SessionLocal() as db:
        claimed = claim_next_job(db, "worker-a")
    assert claimed is not None
    _, generation = claimed

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        run.lease_expires_at = utc_now() + timedelta(seconds=1)
        db.commit()
        before = _naive(run.lease_expires_at)

    with LeaseHeartbeat(
        session_factory=SessionLocal,
        run_id=run_id,
        owner="worker-a",
        generation=generation,
        interval_s=0.05,
        ttl_s=30.0,
    ) as heartbeat:
        sleep(0.3)

    assert heartbeat.renewals > 0, "heartbeat не продлевал аренду"
    assert heartbeat.lost is False
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert _naive(run.lease_expires_at) > before
    _delete(run_id)


def test_heartbeat_stops_when_the_lease_is_reassigned(client):
    run_id = _queued_run()
    with SessionLocal() as db:
        claimed = claim_next_job(db, "worker-a")
    assert claimed is not None
    _, generation = claimed

    with LeaseHeartbeat(
        session_factory=SessionLocal,
        run_id=run_id,
        owner="worker-a",
        generation=generation,
        interval_s=0.05,
        ttl_s=30.0,
    ) as heartbeat:
        with SessionLocal() as db:
            run = db.get(SearchRun, run_id)
            grant_lease(run, "worker-b")
            db.commit()
        sleep(0.3)

    assert heartbeat.lost is True, "heartbeat не заметил перевыдачу аренды"
    _delete(run_id)


def test_stale_worker_does_not_overwrite_the_new_owner_result(client):
    """Fencing: ожившая задача старого worker не портит чужой результат."""
    run_id = _queued_run()
    reassigned = threading.Event()

    def slow_executor(data, db, user, *, search_run):
        # Пока этап выполняется, аренду забирает другой worker.
        with SessionLocal() as other:
            run = other.get(SearchRun, run_id)
            grant_lease(run, "worker-new")
            run.result_payload = {"results": [], "written_by": "worker-new"}
            other.commit()
        reassigned.set()
        return {
            "search_run_id": search_run.id,
            "query": "stale",
            "queries_used": ["stale"],
            "results": [],
            "written_by": "worker-stale",
        }

    processed = process_next_job(executor=slow_executor)
    assert processed == run_id
    assert reassigned.is_set()

    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        assert run.result_payload["written_by"] == "worker-new", (
            "устаревший worker перезаписал результат нового владельца"
        )
        assert run.lease_owner == "worker-new"
        assert run.status != "failed", (
            "чужая задача не должна помечаться failed устаревшим worker"
        )
    _delete(run_id)
