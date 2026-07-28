"""Справочник веществ запоминает экспертные решения и переиспользует их."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_substances.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_substances.db"):
        os.remove("test_substances.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_substances.db"):
        os.remove("test_substances.db")


def _auth(client: TestClient, username: str = "ivanov") -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "demo123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_rfq(client: TestClient, headers: dict[str, str], cas: str, name: str):
    response = client.post(
        "/rfq?verify=false",
        headers=headers,
        json={"cas": cas, "name": name, "incoterms": ["CIP"]},
    )
    assert response.status_code == 201
    return response.json()


def test_confirmed_identity_is_saved_and_reused_by_new_request(client):
    buyer = _auth(client)
    rfq = _create_rfq(
        client,
        buyer,
        "50-78-2",
        "Ацетилсалициловая кислота",
    )

    decision = client.post(
        f"/substances/rfq/{rfq['id']}/decision",
        headers=buyer,
        json={
            "action": "confirm",
            "suggested_name": "Acetylsalicylic acid",
            "preferred_name": "Ацетилсалициловая кислота",
            "synonyms": ["Aspirin", "Acetylsalicylic acid", "Aspirin"],
            "note": "Русское и английское названия считаем эквивалентными.",
        },
    )
    assert decision.status_code == 200
    substance = decision.json()
    assert substance["review_status"] == "confirmed"
    assert substance["preferred_name"] == "Ацетилсалициловая кислота"
    assert substance["synonyms"].count("Aspirin") == 1
    assert substance["request_count"] >= 1

    linked = client.get(f"/rfq/{rfq['id']}", headers=buyer).json()
    assert linked["substance_id"] == substance["id"]
    assert linked["substance_review_status"] == "confirmed"

    reused = client.post(
        "/rfq?verify=false&start_search=true",
        headers=buyer,
        json={
            "substance_id": substance["id"],
            "cas": "64-17-5",
            "name": "Это значение должно быть заменено",
            "incoterms": ["CIP"],
            "search_countries": ["Китай"],
        },
    )
    assert reused.status_code == 201
    request = reused.json()
    assert request["cas"] == "50-78-2"
    assert request["name"] == "Ацетилсалициловая кислота"
    assert request["substance_id"] == substance["id"]

    runs = client.get(
        f"/search-runs?rfq_id={request['id']}",
        headers=buyer,
    ).json()
    payload = runs[0]["input_payload"]
    assert payload["catalog_preferred_name"] == "Ацетилсалициловая кислота"
    assert "Aspirin" in payload["known_synonyms"]
    assert payload["excluded_names"] == []


def test_rejected_suggestion_is_excluded_from_future_search_rules(client):
    buyer = _auth(client)
    rfq = _create_rfq(client, buyer, "64-17-5", "Этанол")
    decision = client.post(
        f"/substances/rfq/{rfq['id']}/decision",
        headers=buyer,
        json={
            "action": "reject",
            "suggested_name": "Methanol",
            "preferred_name": "Этанол",
            "note": "Предложенное название относится к другому веществу.",
        },
    )
    assert decision.status_code == 200
    substance = decision.json()
    assert substance["review_status"] == "confirmed"
    assert "Methanol" in substance["excluded_names"]
    assert "Methanol" not in substance["synonyms"]


def test_catalog_rules_can_be_edited_and_auditor_is_read_only(client):
    buyer = _auth(client)
    created = client.post(
        "/substances",
        headers=buyer,
        json={
            "cas": "7732-18-5",
            "preferred_name": "Вода",
            "synonyms": ["Water"],
        },
    )
    assert created.status_code == 201
    substance_id = created.json()["id"]

    edited = client.patch(
        f"/substances/{substance_id}",
        headers=buyer,
        json={
            "synonyms": ["Глицин", "Glycine", "Aminoacetic acid"],
            "excluded_names": ["Diglycine"],
            "notes": "Использовать карточку для фармацевтического грейда.",
        },
    )
    assert edited.status_code == 200
    assert "Aminoacetic acid" in edited.json()["synonyms"]
    assert edited.json()["reviewed_by_name"]

    auditor = _auth(client, "auditor")
    forbidden = client.patch(
        f"/substances/{substance_id}",
        headers=auditor,
        json={"notes": "Недопустимое изменение"},
    )
    assert forbidden.status_code == 403
    assert client.get("/substances", headers=auditor).status_code == 200
