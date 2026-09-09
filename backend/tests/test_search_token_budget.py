"""Потолок расхода токенов на один запрос поиска и расход по пользователям.

Ограничение по числу вызовов стоимость не удерживает: один разрешённый
вызов дробится на половины и поштучные дозапросы, когда ответ модели не
поместился в лимит выхода. Счётчик вызовов при этом стоит на месте.
"""

import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_tokens.db")

import pytest
from fastapi.testclient import TestClient

from app.connectors.pubchem import SubstanceInfo
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import AgentRun, SearchRun
from app.services.search_budget import (
    STOP_LLM_BUDGET,
    STOP_TOKEN_BUDGET,
    SearchBudget,
)
from app.services.token_usage import run_tokens, user_tokens


class _SpentClient:
    """Клиент модели с уже потраченными токенами."""

    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _budget(**kwargs) -> SearchBudget:
    defaults = {
        "max_queries": 10,
        "max_page_fetches": 10,
        "max_llm_calls": 10,
        "max_runtime_s": 3600,
    }
    return SearchBudget(**{**defaults, **kwargs})


def test_token_budget_refuses_the_next_call():
    budget = _budget(max_tokens=1000)
    llm = _SpentClient(0, 0)
    budget.count_tokens_of(llm)
    assert budget.refuse_llm_call() is None

    # Разрешённый вызов стоил дороже всего потолка: узнать это заранее
    # нельзя, поэтому отказ достаётся следующему.
    llm.prompt_tokens = 900
    llm.completion_tokens = 300
    assert budget.refuse_llm_call() == STOP_TOKEN_BUDGET
    assert budget.stop_reason == STOP_TOKEN_BUDGET

    snapshot = budget.snapshot()
    assert snapshot["tokens_used"] == 1200
    assert snapshot["prompt_tokens_used"] == 900
    assert snapshot["completion_tokens_used"] == 300
    assert snapshot["max_tokens"] == 1000
    # Вызовов оставалось сколько угодно — остановил именно расход.
    assert snapshot["llm_calls_used"] < snapshot["max_llm_calls"]


def test_token_budget_counts_what_the_call_counter_does_not_see():
    """Дробление пакета не увеличивает счётчик вызовов, но стоит денег."""
    budget = _budget(max_tokens=1000)
    llm = _SpentClient(0, 0)
    budget.count_tokens_of(llm)
    assert budget.refuse_llm_call() is None
    # Один пакет развалился на две половины и два поштучных дозапроса.
    for _ in range(4):
        llm.prompt_tokens += 300
    assert budget.llm_calls_used == 1
    assert budget.refuse_llm_call() == STOP_TOKEN_BUDGET


def test_token_budget_continues_where_the_previous_phase_stopped():
    """Лимит задан на запрос, а не на этап: перенос расхода обязателен."""
    budget = _budget(max_tokens=1000, carried_prompt_tokens=900)
    budget.count_tokens_of(_SpentClient(150, 0))
    assert budget.tokens_used == 1050
    assert budget.refuse_llm_call() == STOP_TOKEN_BUDGET


def test_zero_token_budget_means_no_limit():
    budget = _budget(max_tokens=0)
    budget.count_tokens_of(_SpentClient(10_000_000, 10_000_000))
    assert budget.refuse_llm_call() is None
    assert budget.snapshot()["tokens_used"] == 20_000_000


def test_call_budget_still_refuses_under_its_own_name():
    budget = _budget(max_llm_calls=1, max_tokens=1_000_000)
    budget.count_tokens_of(_SpentClient(10, 10))
    assert budget.refuse_llm_call() is None
    assert budget.refuse_llm_call() == STOP_LLM_BUDGET


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_search_tokens.db"):
        os.remove("test_search_tokens.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_search_tokens.db"):
        os.remove("test_search_tokens.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def _fresh_settings(monkeypatch):
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stub_pubchem(monkeypatch):
    monkeypatch.setattr(
        "app.api.supplier_search.PubChemConnector.verify_cas",
        lambda self, cas: SubstanceInfo(
            cas=cas,
            found=True,
            cid=2244,
            iupac_name="2-acetyloxybenzoic acid",
            molecular_formula="C9H8O4",
            molecular_weight=180.16,
            synonyms=["Aspirin", "Acetylsalicylic acid"],
        ),
    )


def test_spent_tokens_stop_the_run_and_leave_a_partial_result(
    client, _fresh_settings
):
    """Первый вызов укладывается в потолок, второй уже нет."""
    monkeypatch = _fresh_settings
    monkeypatch.setenv("SEARCH_MAX_TOKENS", "500")
    get_settings.cache_clear()

    schemas_asked: list[str] = []

    def response(self, **kwargs):
        schemas_asked.append(kwargs["schema_name"])
        # Ответ стоит денег независимо от того, что в нём написано.
        self.prompt_tokens += 400
        self.completion_tokens += 200
        return {
            "canonical_name": "2-acetyloxybenzoic acid",
            "search_names": ["Aspirin", "Acetylsalicylic acid"],
            "input_name_matches": True,
            "substance_type": "single_substance",
            "ambiguities": [],
        }

    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json", response
    )
    monkeypatch.setattr(
        "app.api.supplier_search.search_web",
        lambda query, limit: [
            {
                "title": "Example Chemical Manufacturer",
                "url": "https://manufacturer.example/products/aspirin",
                "snippet": "Official product page",
            }
        ],
    )
    result = client.post(
        "/supplier-search",
        headers=_auth(client, "ivanov"),
        json={"cas": "50-78-2", "name": "Аспирин", "country": "Китай"},
    )

    assert result.status_code == 200
    payload = result.json()
    # Идентичность прошла, планировщик уже нет: расход кончился между ними.
    assert schemas_asked == ["substance_identity"]
    assert payload["ai_used"] is False
    assert payload["fallback_used"] is True
    assert payload["results"]
    assert payload["budget"]["tokens_used"] == 600
    assert payload["budget"]["max_tokens"] == 500

    trace = client.get(
        f"/search-runs/{payload['search_run_id']}",
        headers=_auth(client, "ivanov"),
    ).json()
    planner = next(
        stage
        for stage in trace["agent_runs"]
        if stage["agent_slug"] == "search_planner"
    )
    # Остановка по бюджету — безопасный частичный результат, а не ошибка
    # этапа. И названа она тем лимитом, который действительно кончился:
    # вызовов оставалось сколько угодно.
    assert planner["error"] is None
    reason = planner["output_payload"]["fallback_reason"]
    assert STOP_TOKEN_BUDGET in reason
    assert "токенов" in reason

    identity = next(
        stage
        for stage in trace["agent_runs"]
        if stage["agent_slug"] == "substance_identity"
    )
    assert identity["prompt_tokens"] == 400
    assert identity["completion_tokens"] == 200


def test_run_tokens_are_read_from_the_trace(client):
    """Перенос расхода между этапами запуска опирается на трассу.

    Поиск и проверка кандидатов приходят разными HTTP-запросами: в памяти
    процесса расход первого этапа второму недоступен.
    """
    now = datetime.now(timezone.utc)
    owner_id = client.get("/auth/me", headers=_auth(client, "ivanov")).json()[
        "id"
    ]
    with SessionLocal() as db:
        run = SearchRun(
            owner_id=owner_id,
            status="completed",
            mode="expert",
            input_payload={"cas": "50-78-2"},
            started_at=now,
            completed_at=now,
        )
        db.add(run)
        db.flush()
        db.add_all(
            [
                AgentRun(
                    search_run_id=run.id,
                    sequence=1,
                    agent_slug="substance_identity",
                    agent_name="Идентичность вещества",
                    execution_type="llm",
                    status="completed",
                    prompt_tokens=1200,
                    completion_tokens=300,
                    started_at=now,
                    completed_at=now,
                ),
                # Детерминированный этап токенов не тратит: None, а не ноль.
                AgentRun(
                    search_run_id=run.id,
                    sequence=2,
                    agent_slug="substance_lookup",
                    agent_name="Проверка CAS в PubChem",
                    execution_type="tool",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                ),
            ]
        )
        db.commit()
        assert run_tokens(db, run.id) == (1200, 300)


def test_settings_show_what_each_user_spent(client):
    """Раздел «Настройки»: кто расходует бюджет, видно по накопленной сумме."""
    users = client.get("/users", headers=_auth(client, "admin")).json()
    by_login = {user["username"]: user for user in users}

    # База общая для всего набора, поэтому сравнение идёт с расходом,
    # посчитанным по трассе, а не с числом из этого файла: соседний модуль
    # добавляет ivanov запуски, о которых здесь ничего не известно.
    spender = by_login["ivanov"]
    with SessionLocal() as db:
        usage = user_tokens(db, [spender["id"]])[spender["id"]]
    assert spender["prompt_tokens"] == usage["prompt_tokens"]
    assert spender["completion_tokens"] == usage["completion_tokens"]
    assert spender["total_tokens"] == usage["total_tokens"]
    assert spender["search_runs"] == usage["search_runs"]
    # Оба запуска этого файла: свой поиск и дописанный в трассу вручную.
    assert spender["total_tokens"] >= 400 + 200 + 1200 + 300
    assert spender["search_runs"] >= 2

    for user in users:
        assert (
            user["total_tokens"]
            == user["prompt_tokens"] + user["completion_tokens"]
        )
        # Кто поиск не запускал, тот ничего и не потратил.
        if user["search_runs"] == 0:
            assert user["total_tokens"] == 0
