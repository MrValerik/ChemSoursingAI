"""Бюджеты этапа поиска: лимиты, stop reasons и безопасная остановка."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_budget.db")

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import engine
from app.connectors.pubchem import SubstanceInfo
from app.main import app
from app.services.search_budget import (
    STOP_LLM_BUDGET,
    STOP_PAGE_BUDGET,
    STOP_QUERY_BUDGET,
    STOP_RUNTIME_BUDGET,
    SearchBudget,
)


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_search_budget.db"):
        os.remove("test_search_budget.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_search_budget.db"):
        os.remove("test_search_budget.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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


@pytest.fixture
def _fresh_settings(monkeypatch):
    """Пересобрать кэш настроек до и после переопределения окружения."""
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _mock_search_agents(monkeypatch):
    def response(self, **kwargs):
        if kwargs["schema_name"] == "market_aliases":
            # Марки и другие номера — знание агента, и в тестах его нет.
            return {"alternative_cas": [], "grade_names": []}
        if kwargs["schema_name"] == "substance_identity":
            return {
                "canonical_name": "2-acetyloxybenzoic acid",
                "search_names": ["Aspirin", "Acetylsalicylic acid"],
                "input_name_matches": True,
                "substance_type": "single_substance",
                "ambiguities": [],
            }
        if kwargs["schema_name"] == "supplier_search_plan":
            return {
                "queries": [
                    {
                        "query": '"Aspirin" "50-78-2" manufacturer China',
                        "language": "en",
                        "purpose": "manufacturer",
                        "source_type": "official_site",
                        "priority": 1,
                    }
                ]
            }
        raise AssertionError(f"Unexpected schema: {kwargs['schema_name']}")

    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json", response
    )


def test_budget_counts_and_refuses_in_order():
    budget = SearchBudget(
        max_queries=2,
        max_page_fetches=1,
        max_llm_calls=1,
        max_runtime_s=3600,
    )
    assert budget.refuse_query() is None
    assert budget.refuse_query() is None
    assert budget.refuse_query() == STOP_QUERY_BUDGET
    assert budget.refuse_page_fetch() is None
    assert budget.refuse_page_fetch() == STOP_PAGE_BUDGET
    assert budget.refuse_llm_call() is None
    assert budget.refuse_llm_call() == STOP_LLM_BUDGET
    # Первый отказ фиксируется как причина остановки этапа.
    assert budget.stop_reason == STOP_QUERY_BUDGET
    snapshot = budget.snapshot()
    assert snapshot["queries_used"] == 2
    assert snapshot["page_fetches_used"] == 1
    assert snapshot["llm_calls_used"] == 1
    assert snapshot["stop_reason"] == STOP_QUERY_BUDGET


def test_runtime_budget_refuses_every_operation():
    budget = SearchBudget(
        max_queries=10,
        max_page_fetches=10,
        max_llm_calls=10,
        max_runtime_s=0,
    )
    assert budget.refuse_query() == STOP_RUNTIME_BUDGET
    assert budget.refuse_page_fetch() == STOP_RUNTIME_BUDGET
    assert budget.refuse_llm_call() == STOP_RUNTIME_BUDGET
    assert budget.queries_used == 0
    assert budget.stop_reason == STOP_RUNTIME_BUDGET


def test_query_budget_stops_search_with_partial_result(
    client, _fresh_settings
):
    monkeypatch = _fresh_settings
    monkeypatch.setenv("SEARCH_MAX_QUERIES", "1")
    get_settings.cache_clear()
    _mock_search_agents(monkeypatch)
    executed_queries = []

    def fake_search(query, limit):
        executed_queries.append(query)
        return [
            {
                "title": "Example Chemical Manufacturer",
                "url": "https://manufacturer.example/products/aspirin",
                "snippet": "Official product page",
            }
        ]

    monkeypatch.setattr("app.api.supplier_search.search_web", fake_search)
    response = client.post(
        "/supplier-search",
        headers=_auth(client, "ivanov"),
        json={"cas": "50-78-2", "name": "Аспирин", "country": "Китай"},
    )

    assert response.status_code == 200
    payload = response.json()
    # Для Китая план требует минимум пять запросов, но бюджет разрешил один.
    assert len(executed_queries) == 1
    assert payload["queries_used"] == executed_queries
    assert payload["stop_reason"] == STOP_QUERY_BUDGET
    assert payload["budget"]["queries_used"] == 1
    assert payload["results"]

    trace = client.get(
        f"/search-runs/{payload['search_run_id']}",
        headers=_auth(client, "ivanov"),
    ).json()
    web_stage = next(
        stage
        for stage in trace["agent_runs"]
        if stage["agent_slug"] == "web_search"
    )
    assert web_stage["output_payload"]["stop_reason"] == STOP_QUERY_BUDGET
    assert web_stage["output_payload"]["budget"]["max_queries"] == 1
    assert web_stage["error"] is None


def test_llm_budget_falls_back_to_deterministic_plan(
    client, _fresh_settings
):
    monkeypatch = _fresh_settings
    monkeypatch.setenv("SEARCH_MAX_LLM_CALLS", "0")
    get_settings.cache_clear()

    def unexpected_llm(self, **kwargs):
        raise AssertionError("LLM must not be called with a zero budget")

    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json", unexpected_llm
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
    response = client.post(
        "/supplier-search",
        headers=_auth(client, "ivanov"),
        json={"cas": "50-78-2", "name": "Аспирин", "country": "Китай"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_used"] is False
    assert payload["fallback_used"] is True
    assert payload["results"]
    assert payload["budget"]["llm_calls_used"] == 0


def test_agent_event_log_is_recorded_for_each_stage(client, _fresh_settings):
    monkeypatch = _fresh_settings
    _mock_search_agents(monkeypatch)
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
    response = client.post(
        "/supplier-search",
        headers=_auth(client, "ivanov"),
        json={"cas": "50-78-2", "name": "Аспирин", "country": "Китай"},
    )
    assert response.status_code == 200

    trace = client.get(
        f"/search-runs/{response.json()['search_run_id']}",
        headers=_auth(client, "ivanov"),
    ).json()
    events_by_slug = {
        stage["agent_slug"]: stage["events"] or []
        for stage in trace["agent_runs"]
    }
    assert any(
        "PubChem подтвердил" in event["message"]
        for event in events_by_slug["substance_lookup"]
    )
    assert any(
        "каноническое имя" in event["message"]
        for event in events_by_slug["substance_identity"]
    )
    assert any(
        "Итоговый план" in event["message"]
        for event in events_by_slug["search_planner"]
    )
    web_messages = [e["message"] for e in events_by_slug["web_search"]]
    assert any(m.startswith("Ищу:") for m in web_messages)
    assert any("отобрано" in m for m in web_messages)
    for stage in trace["agent_runs"]:
        for event in stage["events"] or []:
            assert event["at"]
            assert event["kind"] in {"action", "info", "warning", "error"}


def test_page_text_budget_shrinks_with_a_small_model_context(_fresh_settings):
    from app.api.supplier_search import _PAGE_TEXT_HARD_LIMIT, _page_text_budget

    monkeypatch = _fresh_settings
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "12288")
    get_settings.cache_clear()
    assert _page_text_budget() == _PAGE_TEXT_HARD_LIMIT

    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "4096")
    get_settings.cache_clear()
    small = _page_text_budget()
    assert 0 < small < _PAGE_TEXT_HARD_LIMIT

    # Даже при абсурдно маленьком контексте бюджет остаётся положительным,
    # чтобы этап отдал меньше текста, а не упал.
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "512")
    get_settings.cache_clear()
    assert _page_text_budget() > 0
