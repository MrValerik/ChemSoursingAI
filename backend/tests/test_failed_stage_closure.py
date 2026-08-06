"""Упавший прогон не оставляет этап крутиться.

Прогон 51 по адипиновой кислоте упал на оценке, а сама оценка осталась в
состоянии «Выполняется»: по трассе нельзя было понять, где оборвалось, и
интерфейс показывал вечный спиннер рядом с красной ошибкой.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_failed_stage_closure.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import SearchRun, User
from app.models.search_trace import AgentRun
from app.search_worker import _close_running_stages
from app.services.search_trace import create_search_run, start_agent_run


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_failed_stage_closure.db"):
        os.remove("test_failed_stage_closure.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_failed_stage_closure.db"):
        os.remove("test_failed_stage_closure.db")


def _run_with_stages() -> tuple[int, int, int]:
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "124-04-9", "name": "Adipic acid"},
            mode="queued_search",
            status="running",
        )
        db.commit()
        done, _ = start_agent_run(
            db,
            search_run=run,
            sequence=1,
            agent_slug="supplier_qualification",
            agent_name="Оценка",
            execution_type="llm",
        )
        done.status = "completed"
        stuck, _ = start_agent_run(
            db,
            search_run=run,
            sequence=2,
            agent_slug="supplier_verifier",
            agent_name="Аудит",
            execution_type="llm",
        )
        db.commit()
        return run.id, done.id, stuck.id


def test_the_running_stage_is_marked_failed(client):
    run_id, done_id, stuck_id = _run_with_stages()

    with SessionLocal() as db:
        _close_running_stages(db, run_id, "ответ не поместился в лимит выхода")
        db.commit()

    with SessionLocal() as db:
        stuck = db.get(AgentRun, stuck_id)
        assert stuck.status == "failed"
        assert "лимит выхода" in stuck.error
        assert stuck.completed_at is not None
        # Уже завершённый этап не переписывается.
        assert db.get(AgentRun, done_id).status == "completed"

    # База у тестов общая, а незавершённый прогон попадёт в чужие подсчёты
    # брошенных задач.
    with SessionLocal() as db:
        db.delete(db.get(SearchRun, run_id))
        db.commit()
