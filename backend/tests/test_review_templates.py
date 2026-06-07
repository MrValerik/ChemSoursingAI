"""Тесты шага 5: очередь ручного разбора, назначение, шаблоны."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_review.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_review.db"):
        os.remove("test_review.db")
    with TestClient(app) as c:
        yield c
    if os.path.exists("test_review.db"):
        os.remove("test_review.db")


def _login(client, username="ivanov"):
    resp = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_review_queue_and_assignment(client):
    ivanov = _login(client)
    head = _login(client, "petrova")

    rfq = client.post(
        "/rfq?verify=false",
        json={"cas": "50-78-2", "name": "Aspirin", "incoterms": ["CIP"]},
        headers=ivanov,
    ).json()
    esc = client.post(
        f"/rfq/{rfq['id']}/escalate",
        json={"reason": "shortage", "note": "Дефицит"},
        headers=ivanov,
    ).json()

    # Очередь обогащена данными запроса.
    queue = client.get("/escalations", headers=head).json()
    row = next(e for e in queue if e["id"] == esc["id"])
    assert row["rfq_name"] == "Aspirin"
    assert row["rfq_owner_name"] == "Иван Иванов"

    # Руководитель переназначает; статус -> in_progress.
    updated = client.patch(
        f"/escalations/{esc['id']}",
        json={"assignee": "Анна Петрова"},
        headers=head,
    ).json()
    assert updated["assignee"] == "Анна Петрова"
    assert updated["status"] == "in_progress"

    # Закупщик не может назначить другого.
    resp = client.patch(
        f"/escalations/{esc['id']}",
        json={"assignee": "Кто-то Другой"},
        headers=ivanov,
    )
    assert resp.status_code == 403

    # Закрытие кейса возвращает RFQ из ручного разбора.
    client.patch(
        f"/escalations/{esc['id']}",
        json={"status": "resolved", "note": "Решено: нашли альтернативу"},
        headers=head,
    )
    assert client.get(f"/rfq/{rfq['id']}", headers=ivanov).json()["status"] == "collecting"


def test_users_list_role_gate(client):
    assert client.get("/users", headers=_login(client, "petrova")).status_code == 200
    assert client.get("/users", headers=_login(client)).status_code == 403


def test_templates_crud_and_versions(client):
    ivanov = _login(client)
    head = _login(client, "petrova")

    templates = client.get("/templates", headers=ivanov).json()
    assert len(templates) >= 3
    wa = next(t for t in templates if t["kind"] == "whatsapp")
    assert wa["moderation"] == "pending"

    # Закупщик не может править.
    resp = client.patch(
        f"/templates/{wa['id']}", json={"body": "new"}, headers=ivanov
    )
    assert resp.status_code == 403

    # Правка руководителем: версия растёт, WhatsApp уходит в черновик.
    updated = client.patch(
        f"/templates/{wa['id']}", json={"body": "Hello v2"}, headers=head
    ).json()
    assert updated["version"] == 2
    assert updated["moderation"] == "draft"

    # Создание нового шаблона.
    resp = client.post(
        "/templates",
        json={"kind": "reply", "name": "Тест", "body": "Текст"},
        headers=head,
    )
    assert resp.status_code == 201
