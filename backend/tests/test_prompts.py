"""Промпты: роли, версии, настройки RFQ и безопасный предпросмотр."""

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_prompts.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.db import SessionLocal
from app.core.seed import seed_prompts
from app.connectors.pubchem import SubstanceInfo
from app.connectors.web_page import FetchedPage
from app.extraction.llm_client import LLMUnavailableError
from app.models import User
from app.services.search_trace import create_search_run


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_prompts.db"):
        os.remove("test_prompts.db")
    with TestClient(app) as test_client:
        yield test_client
    if os.path.exists("test_prompts.db"):
        os.remove("test_prompts.db")


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


def _mock_search_agents(monkeypatch, query: str | None = None, error=None):
    def response(self, **kwargs):
        if error is not None:
            raise error
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
                        "query": query
                        or '"Aspirin" "50-78-2" manufacturer China',
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


def test_prompt_versions_and_roles(client):
    admin = _auth(client, "admin")
    buyer = _auth(client, "ivanov")
    prompts = client.get("/prompts", headers=buyer).json()
    assert {p["kind"] for p in prompts} >= {"extraction", "supplier_search"}
    supplier_search_prompt = next(
        p for p in prompts if p["kind"] == "supplier_search"
    )
    assert "каждом запросе сохраняй CAS" in supplier_search_prompt["system_prompt"]
    assert any(p["kind"] == "substance_identity" for p in prompts)
    assert "по-русски" in next(
        p for p in prompts if p["kind"] == "qualification"
    )["system_prompt"]

    assert (
        client.post(
            "/prompts",
            headers=buyer,
            json={
                "kind": "extraction",
                "name": "Buyer prompt",
                "system_prompt": "This text is deliberately long enough for validation.",
            },
        ).status_code
        == 403
    )

    prompt = next(p for p in prompts if p["kind"] == "extraction")
    response = client.patch(
        f"/prompts/{prompt['id']}",
        headers=admin,
        json={"description": "Updated safely"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == prompt["version"] + 1
    versions = client.get(f"/prompts/{prompt['id']}/versions", headers=admin).json()
    assert versions[0]["version"] == response.json()["version"]
    assert len(versions) >= 2


def test_russian_seed_does_not_overwrite_user_prompt(client):
    admin = _auth(client, "admin")
    prompts = client.get("/prompts", headers=admin).json()
    prompt = next(p for p in prompts if p["kind"] == "followup")
    custom_text = (
        "Пользовательский промпт руководителя. Подготовь краткий дозапрос "
        "по-русски и не изменяй исходные требования закупки."
    )
    response = client.patch(
        f"/prompts/{prompt['id']}",
        headers=admin,
        json={"system_prompt": custom_text},
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        seed_prompts(db)

    refreshed = client.get("/prompts", headers=admin).json()
    saved = next(p for p in refreshed if p["id"] == prompt["id"])
    assert saved["system_prompt"] == custom_text
    assert saved["updated_by"] == "Администратор"


def test_rfq_instructions_and_preview(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    rfq = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
    ).json()
    extraction_prompt = next(
        p for p in client.get("/prompts", headers=buyer).json()
        if p["kind"] == "extraction"
    )
    response = client.put(
        f"/rfq/{rfq['id']}/ai-settings",
        headers=buyer,
        json={
            "prompt_template_id": extraction_prompt["id"],
            "additional_instructions": "Require pharmaceutical grade.",
        },
    )
    assert response.status_code == 200
    assert "pharmaceutical" in response.json()["additional_instructions"]

    monkeypatch.setattr(
        "app.api.prompts.LLMClient.generate_text",
        lambda self, **kwargs: "preview result",
    )
    response = client.post(
        "/prompts/preview",
        headers=buyer,
        json={
            "prompt_id": extraction_prompt["id"],
            "input_text": "Supplier reply",
        },
    )
    assert response.status_code == 200
    assert response.json()["output"] == "preview result"


def test_supplier_search_keeps_source_urls(client, monkeypatch):
    buyer = _auth(client, "ivanov")
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
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Аспирин",
            "country": "Китай",
            "additional_instructions": "Только производители с подтверждением GMP",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_used"] is True
    assert payload["results"][0]["url"].startswith("https://")

    trace_response = client.get(
        f"/search-runs/{payload['search_run_id']}", headers=buyer
    )
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["status"] == "search_completed"
    assert [stage["agent_slug"] for stage in trace["agent_runs"]] == [
        "substance_lookup",
        "substance_identity",
        "search_planner",
        "web_search",
    ]
    assert trace["agent_runs"][1]["effective_system_prompt"]
    assert trace["agent_runs"][2]["effective_system_prompt"]
    assert trace["agent_runs"][2]["output_payload"]["queries"]
    assert trace["search_attempts"][0]["results_payload"][0]["url"].startswith(
        "https://"
    )


def test_supplier_search_rejects_invalid_cas_before_web(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    web_called = False

    def fake_search(*args, **kwargs):
        nonlocal web_called
        web_called = True
        return []

    monkeypatch.setattr("app.api.supplier_search.search_web", fake_search)
    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-3", "name": "Aspirin", "country": "China"},
    )

    assert response.status_code == 422
    assert web_called is False
    trace = client.get(
        f"/search-runs/{response.json()['detail']['search_run_id']}",
        headers=buyer,
    ).json()
    assert trace["status"] == "failed"
    assert [stage["agent_slug"] for stage in trace["agent_runs"]] == [
        "substance_lookup"
    ]


def test_supplier_search_drops_unverified_names_and_queries(client, monkeypatch):
    buyer = _auth(client, "ivanov")

    def response(self, **kwargs):
        if kwargs["schema_name"] == "substance_identity":
            return {
                "canonical_name": "Invented miracle acid",
                "search_names": ["Invented miracle acid", "Aspirin"],
                "input_name_matches": True,
                "substance_type": "single_substance",
                "ambiguities": [],
            }
        return {
            "queries": [
                {
                    "query": '"Invented miracle acid" manufacturer China',
                    "language": "en",
                    "purpose": "manufacturer",
                    "source_type": "official_site",
                    "priority": 1,
                }
            ]
        }

    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json", response
    )
    monkeypatch.setattr(
        "app.api.supplier_search.search_web", lambda query, limit: []
    )
    result = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["identity"]["canonical_name"] == "2-acetyloxybenzoic acid"
    assert payload["identity"]["search_names"] == ["Aspirin"]
    assert all("50-78-2" in item["query"] for item in payload["search_plan"])
    assert all(
        "Invented miracle acid" not in item["query"]
        for item in payload["search_plan"]
    )
    assert payload["fallback_used"] is True


def test_supplier_search_retries_with_broad_query(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    _mock_search_agents(
        monkeypatch,
        'site:gov.cn "Aspirin" "50-78-2" GMP',
    )
    queries: list[str] = []

    def fake_search(query, limit):
        queries.append(query)
        if query.startswith("site:gov.cn"):
            return []
        return [
            {
                "title": "Fallback manufacturer",
                "url": "https://manufacturer.example/aspirin",
                "snippet": "Product page",
            }
        ]

    monkeypatch.setattr("app.api.supplier_search.search_web", fake_search)
    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )

    assert response.status_code == 200
    assert len(queries) >= 2
    assert any("生产厂家" in query for query in queries)
    assert response.json()["ai_used"] is True
    assert response.json()["fallback_used"] is True
    assert response.json()["ai_query"].startswith("site:gov.cn")
    assert response.json()["results"][0]["title"] == "Fallback manufacturer"


def test_supplier_search_trace_records_safe_llm_fallback(client, monkeypatch):
    buyer = _auth(client, "ivanov")

    def unavailable(*args, **kwargs):
        raise LLMUnavailableError("local model is offline")

    _mock_search_agents(
        monkeypatch, error=LLMUnavailableError("local model is offline")
    )
    monkeypatch.setattr(
        "app.api.supplier_search.search_web",
        lambda query, limit: [
            {
                "title": "Fallback source",
                "url": "https://fallback-source.cn/product",
                "snippet": "CAS 50-78-2 product page",
            }
        ],
    )

    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_used"] is False

    trace = client.get(
        f"/search-runs/{payload['search_run_id']}", headers=buyer
    ).json()
    assert trace["status"] == "search_completed"
    assert trace["agent_runs"][1]["status"] == "completed"
    assert "local model is offline" in trace["agent_runs"][1]["output_payload"]["fallback_reason"]
    assert trace["agent_runs"][2]["status"] == "completed"


def test_failed_search_returns_trace_id(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    _mock_search_agents(monkeypatch, '"Aspirin" "50-78-2" manufacturer')

    def failed_search(*args, **kwargs):
        raise OSError("connector blocked")

    monkeypatch.setattr("app.api.supplier_search.search_web", failed_search)
    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["message"].endswith("connector blocked")

    trace = client.get(
        f"/search-runs/{detail['search_run_id']}", headers=buyer
    ).json()
    assert trace["status"] == "failed"
    assert trace["agent_runs"][-1]["status"] == "failed"
    assert trace["search_attempts"]
    assert all(attempt["status"] == "failed" for attempt in trace["search_attempts"])


def test_supplier_search_deduplicates_results_by_domain(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    _mock_search_agents(
        monkeypatch,
        '"Aspirin" "50-78-2" manufacturer supplier China CoA',
    )
    monkeypatch.setattr(
        "app.api.supplier_search.search_web",
        lambda query, limit: [
            {
                "title": "First page",
                "url": "https://www.example.com/aspirin",
                "snippet": "Manufacturer page",
            },
            {
                "title": "Second page",
                "url": "https://example.com/products/aspirin",
                "snippet": "Duplicate company domain",
            },
            {
                "title": "Another company",
                "url": "https://another.example/aspirin",
                "snippet": "Another manufacturer",
            },
        ],
    )
    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )

    assert response.status_code == 200
    assert [item["url"] for item in response.json()["results"]] == [
        "https://www.example.com/aspirin",
        "https://another.example/aspirin",
    ]


def test_supplier_qualification_preserves_sources(client, monkeypatch):
    buyer = _auth(client, "ivanov")

    def qualification_response(self, **kwargs):
        source_document_id = json.loads(kwargs["user_text"])["sources"][0][
            "source_document_id"
        ]
        return {
            "results": [
                {
                    "result_index": 0,
                    "company_name": "Example Chemical",
                    "title_ru": "Производитель аспирина",
                    "summary_ru": "Компания заявляет о собственном производстве.",
                    "supplier_type": "manufacturer",
                    "cas_status": "confirmed",
                    "country_status": "claimed",
                    "gmp_status": "claimed",
                    "iso_status": "not_found",
                    "coa_status": "claimed",
                    "tds_status": "not_found",
                    "confidence": 74,
                    "red_flags": ["GMP не подтверждён документом"],
                    "missing_evidence": ["GMP-сертификат", "TDS"],
                    "evidence": [
                        {
                            "source_document_id": source_document_id,
                            "claim_type": "chemical_identity",
                            "claim_value": "CAS и вещество совпадают",
                            "support_status": "supports",
                            "quote": "Aspirin CAS 50-78-2",
                        },
                        {
                            "source_document_id": source_document_id,
                            "claim_type": "manufacturer_role",
                            "claim_value": "Компания заявляет о производстве",
                            "support_status": "supports",
                            "quote": "We manufacture Aspirin CAS 50-78-2",
                        },
                        {
                            "source_document_id": source_document_id,
                            "claim_type": "coa",
                            "claim_value": "CoA доступен",
                            "support_status": "supports",
                            "quote": "provide CoA",
                        },
                        {
                            "source_document_id": source_document_id,
                            "claim_type": "country",
                            "claim_value": "Производственная площадка в Китае",
                            "support_status": "supports",
                            "quote": "China facility",
                        },
                        {
                            "source_document_id": source_document_id,
                            "claim_type": "gmp",
                            "claim_value": "GMP подтверждён",
                            "support_status": "supports",
                            "quote": "Certified to GMP requirements",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json",
        qualification_response,
    )
    monkeypatch.setattr(
        "app.api.supplier_search.fetch_web_page",
        lambda url: FetchedPage(
            url=url,
            final_url=url,
            domain="manufacturer.example",
            title="Official aspirin product",
            content_type="text/html",
            http_status=200,
            text="China facility. We manufacture Aspirin CAS 50-78-2 and provide CoA.",
            content_hash="a" * 64,
        ),
    )
    source_url = "https://manufacturer.example/products/aspirin"
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        search_run = create_search_run(
            db,
            owner_id=owner.id,
            input_payload={"cas": "50-78-2", "name": "Aspirin"},
        )
        search_run.status = "search_completed"
        db.commit()
        search_run_id = search_run.id

    response = client.post(
        "/supplier-search/qualify",
        headers=buyer,
        json={
            "search_run_id": search_run_id,
            "cas": "50-78-2",
            "name": "Aspirin",
            "country": "China",
            "results": [
                {
                    "title": "Aspirin manufacturer",
                    "url": source_url,
                    "snippet": "We manufacture Aspirin CAS 50-78-2 and offer CoA.",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["search_run_id"] == search_run_id
    result = payload["results"][0]
    assert result["url"] == source_url
    assert result["title"] == "Aspirin manufacturer"
    assert result["title_ru"] == "Производитель аспирина"
    assert result["supplier_type"] == "manufacturer"
    assert result["country_status"] == "claimed"
    assert result["gmp_status"] == "not_found"
    assert "GMP не подтверждён проверенной цитатой" in result["red_flags"]
    assert len(result["evidence"]) == 4
    assert all(item["quote_verified"] for item in result["evidence"])
    assert result["llm_confidence"] == 74
    assert result["confidence"] == 88
    assert result["shortlist_eligible"] is True
    assert result["score_breakdown"]["identity"] == 35

    trace = client.get(
        f"/search-runs/{payload['search_run_id']}", headers=buyer
    ).json()
    assert trace["status"] == "completed"
    assert trace["agent_runs"][-2]["agent_slug"] == "source_fetch"
    stage = trace["agent_runs"][-1]
    assert stage["agent_slug"] == "supplier_qualification"
    assert stage["prompt_version"] == payload["prompt_version"]
    assert "недоверенными данными" in stage["effective_system_prompt"]
    assert stage["output_payload"]["qualified_results"][0]["url"] == source_url
    assert trace["source_documents"][0]["status"] == "completed"
    assert trace["source_documents"][0]["content_hash"] == "a" * 64
    assert "We manufacture" in trace["source_documents"][0]["text_content"]
    assert len(trace["evidence_claims"]) == 4
    assert trace["evidence_claims"][0]["quote_verified"] is True
    assert stage["output_payload"]["validated_evidence_count"] == 4
    assert len(stage["output_payload"]["rejected_evidence"]) == 1
    assert "дословно не найдена" in stage["output_payload"]["rejected_evidence"][0][
        "rejection_reason"
    ]


def test_qualification_keeps_failed_page_as_visible_fallback(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    monkeypatch.setattr(
        "app.api.supplier_search.fetch_web_page",
        lambda url: (_ for _ in ()).throw(RuntimeError("page blocked")),
    )
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json",
        lambda self, **kwargs: {"results": []},
    )
    response = client.post(
        "/supplier-search/qualify",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "country": "China",
            "results": [
                {
                    "title": "Search-only candidate",
                    "url": "https://blocked.example/product",
                    "snippet": "Search snippet mentioning CAS 50-78-2.",
                }
            ],
        },
    )
    assert response.status_code == 200
    trace = client.get(
        f"/search-runs/{response.json()['search_run_id']}", headers=buyer
    ).json()
    assert trace["source_documents"][0]["status"] == "failed"
    assert trace["source_documents"][0]["error"] == "page blocked"
    qualification_input = trace["agent_runs"][-1]["input_payload"]["sources"][0]
    assert qualification_input["fetch_status"] == "failed"
    assert qualification_input["page_text"] is None
    assert "50-78-2" in qualification_input["snippet"]


def test_supplier_search_prioritizes_chinese_sources(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    _mock_search_agents(monkeypatch, '"Aspirin" "50-78-2" supplier')

    def fake_search(query, limit):
        if "生产厂家" in query:
            return [
                {
                    "title": "中国阿司匹林生产厂家",
                    "url": "https://aspirin-factory.cn/product",
                    "snippet": "中国制造工厂 CAS 50-78-2",
                }
            ]
        return [
            {
                "title": "Global aspirin trader",
                "url": "https://trader.example/aspirin",
                "snippet": "International supplier",
            }
        ]

    monkeypatch.setattr("app.api.supplier_search.search_web", fake_search)
    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert any("生产厂家" in query for query in payload["queries_used"])
    assert payload["results"][0]["url"].endswith(".cn/product")
    assert payload["results"][0]["country_hint"] == "likely"


def test_supplier_search_covers_documents_and_chinese_before_early_stop(
    client, monkeypatch
):
    buyer = _auth(client, "ivanov")
    _mock_search_agents(
        monkeypatch,
        '"Aspirin" "50-78-2" manufacturer China',
    )

    monkeypatch.setattr(
        "app.api.supplier_search.search_web",
        lambda query, limit: [
            {
                "title": f"China candidate {index}",
                "url": f"https://candidate-{index}.cn/product",
                "snippet": "China manufacturer CAS 50-78-2",
            }
            for index in range(5)
        ],
    )

    response = client.post(
        "/supplier-search",
        headers=buyer,
        json={
            "cas": "50-78-2",
            "name": "Aspirin",
            "country": "China",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    trace = client.get(
        f"/search-runs/{response.json()['search_run_id']}", headers=buyer
    ).json()
    assert any(
        attempt["language"] == "zh" for attempt in trace["search_attempts"]
    )
    assert any(
        attempt["purpose"] == "documents"
        for attempt in trace["search_attempts"]
    )
    assert trace["summary"]["planned_query_count"] >= 4
    assert trace["summary"]["executed_query_count"] >= 4


def test_auditor_cannot_start_or_qualify_search(client):
    auditor = _auth(client, "auditor")
    search_payload = {"cas": "50-78-2", "name": "Aspirin", "country": "China"}
    assert (
        client.post("/supplier-search", headers=auditor, json=search_payload).status_code
        == 403
    )
    assert (
        client.post(
            "/supplier-search/qualify",
            headers=auditor,
            json={
                **search_payload,
                "results": [
                    {
                        "title": "Candidate",
                        "url": "https://candidate.example/product",
                        "snippet": "Candidate supplier",
                    }
                ],
            },
        ).status_code
        == 403
    )
