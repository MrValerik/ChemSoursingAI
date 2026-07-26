"""Промпты: роли, версии, настройки RFQ и безопасный предпросмотр."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_prompts.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def test_prompt_versions_and_roles(client):
    admin = _auth(client, "admin")
    buyer = _auth(client, "ivanov")
    prompts = client.get("/prompts", headers=buyer).json()
    assert {p["kind"] for p in prompts} >= {"extraction", "supplier_search"}

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
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_text",
        lambda self, **kwargs: '"Aspirin" "50-78-2" manufacturer China',
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
        headers=buyer,
        json={"cas": "50-78-2", "name": "Aspirin", "country": "China"},
    )
    assert response.status_code == 200
    assert response.json()["ai_used"] is True
    assert response.json()["results"][0]["url"].startswith("https://")


def test_supplier_search_retries_with_broad_query(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_text",
        lambda self, **kwargs: 'site:gov.cn "Aspirin" "50-78-2" GMP',
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
    assert len(queries) == 2
    assert response.json()["ai_used"] is True
    assert response.json()["fallback_used"] is True
    assert response.json()["ai_query"].startswith("site:gov.cn")
    assert response.json()["results"][0]["title"] == "Fallback manufacturer"


def test_supplier_search_deduplicates_results_by_domain(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_text",
        lambda self, **kwargs: '"Aspirin" "50-78-2" manufacturer supplier China CoA',
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
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json",
        lambda self, **kwargs: {
            "results": [
                {
                    "result_index": 0,
                    "company_name": "Example Chemical",
                    "title_ru": "Производитель аспирина",
                    "summary_ru": "Компания заявляет о собственном производстве.",
                    "supplier_type": "manufacturer",
                    "cas_status": "confirmed",
                    "gmp_status": "claimed",
                    "iso_status": "not_found",
                    "coa_status": "claimed",
                    "tds_status": "not_found",
                    "confidence": 74,
                    "red_flags": ["GMP не подтверждён документом"],
                    "missing_evidence": ["GMP-сертификат", "TDS"],
                }
            ]
        },
    )
    source_url = "https://manufacturer.example/products/aspirin"
    response = client.post(
        "/supplier-search/qualify",
        headers=buyer,
        json={
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
    result = response.json()["results"][0]
    assert result["url"] == source_url
    assert result["title"] == "Aspirin manufacturer"
    assert result["title_ru"] == "Производитель аспирина"
    assert result["supplier_type"] == "manufacturer"
    assert result["gmp_status"] == "claimed"
