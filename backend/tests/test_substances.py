"""Справочник веществ запоминает экспертные решения и переиспользует их."""

import os
from uuid import uuid4

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


def _unique_valid_cas() -> str:
    """Строит синтетический CAS, не конфликтующий с общей тестовой БД."""
    prefix = str(1_000_000 + uuid4().int % 8_000_000)
    middle = "08"
    body = f"{prefix}{middle}"
    check_digit = sum(
        multiplier * int(digit)
        for multiplier, digit in enumerate(reversed(body), start=1)
    ) % 10
    return f"{prefix}-{middle}-{check_digit}"


def test_new_request_registers_substance_and_reuses_it_for_price_history(client):
    buyer = _auth(client)
    cas = _unique_valid_cas()
    first = _create_rfq(client, buyer, cas, "Тестовое вещество")
    second = _create_rfq(client, buyer, cas, "Test substance")

    assert first["substance_id"] is not None
    assert second["substance_id"] == first["substance_id"]

    catalog = client.get("/substances", headers=buyer)
    assert catalog.status_code == 200
    substances = [item for item in catalog.json() if item["cas"] == cas]
    assert len(substances) == 1
    assert substances[0]["review_status"] == "unreviewed"
    assert substances[0]["request_count"] == 2

    linked = client.get(
        f"/substances/{first['substance_id']}/requests",
        headers=buyer,
    )
    assert linked.status_code == 200
    assert {first["id"], second["id"]}.issubset(
        {item["id"] for item in linked.json()}
    )

    quote = client.post(
        "/quotations",
        headers=buyer,
        json={
            "rfq_id": first["id"],
            "price": 12.5,
            "currency": "USD",
            "price_unit": "kg",
            "quoted_quantity": "100 kg",
            "incoterm": "CIP",
            "moq": "25 kg",
        },
    )
    assert quote.status_code == 201

    prices = client.get(
        f"/substances/{first['substance_id']}/price-history",
        headers=buyer,
    )
    assert prices.status_code == 200
    assert prices.json()[0] == {
        "quotation_id": quote.json()["id"],
        "rfq_id": first["id"],
        "quoted_at": quote.json()["created_at"],
        "price": 12.5,
        "currency": "USD",
        "price_unit": "kg",
        "quoted_quantity": "100 kg",
        "incoterm": "CIP",
        "moq": "25 kg",
        "supplier_name": None,
    }

    history = client.get(
        f"/substances/{first['substance_id']}/history",
        headers=buyer,
    )
    assert history.status_code == 200
    created_entries = [
        item for item in history.json() if item["action"] == "created_from_request"
    ]
    assert created_entries
    assert created_entries[0]["source_rfq_id"] is not None

    confirmed = client.post(
        "/substances",
        headers=buyer,
        json={
            "cas": cas,
            "preferred_name": "Тестовое вещество",
            "synonyms": ["Test substance"],
        },
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["id"] == first["substance_id"]
    assert confirmed.json()["review_status"] == "confirmed"

    confirmed_history = client.get(
        f"/substances/{first['substance_id']}/history",
        headers=buyer,
    )
    assert confirmed_history.json()[0]["action"] == "catalog_confirmed"


def test_request_without_cas_is_not_unsafely_merged_into_catalog(client):
    buyer = _auth(client)
    created = client.post(
        "/rfq?verify=false",
        headers=buyer,
        json={
            "identification_method": "spec",
            "name": "Смесь ПАВ по спецификации",
            "specification": "Неионогенная смесь для промышленной мойки",
            "incoterms": ["CIP"],
        },
    )
    assert created.status_code == 201
    assert created.json()["substance_id"] is None


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
    history = client.get(
        f"/substances/{substance['id']}/history",
        headers=buyer,
    )
    assert history.status_code == 200
    assert history.json()[0]["action"] == "identity_confirmed"
    assert history.json()[0]["actor_name"]
    assert history.json()[0]["source_rfq_id"] == rfq["id"]

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
    assert payload["catalog_notes"] == (
        "Русское и английское названия считаем эквивалентными."
    )


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
    history = client.get(
        f"/substances/{substance_id}/history",
        headers=buyer,
    )
    assert history.status_code == 200
    entries = history.json()
    assert [entry["action"] for entry in entries[:2]] == [
        "rules_updated",
        "created",
    ]
    assert entries[0]["changes"]["notes"]["after"] == (
        "Использовать карточку для фармацевтического грейда."
    )
    assert all(entry["actor_name"] for entry in entries)

    auditor = _auth(client, "auditor")
    forbidden = client.patch(
        f"/substances/{substance_id}",
        headers=auditor,
        json={"notes": "Недопустимое изменение"},
    )
    assert forbidden.status_code == 403
    assert client.get("/substances", headers=auditor).status_code == 200
    assert (
        client.get(
            f"/substances/{substance_id}/history",
            headers=auditor,
        ).status_code
        == 200
    )
