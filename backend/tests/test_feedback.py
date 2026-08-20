"""Обратная связь: чего пользователю не хватает и что ему непонятно.

Раздел заведён не как служба поддержки, а как способ узнать словами
самого закупщика, чего в программе нет. Поэтому важны три вещи:
отправленное не пропадает, автор видит своё, а руководитель и
администратор — всё.
"""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_feedback.db")

import pytest
from fastapi.testclient import TestClient

from app.connectors.email import EmailDeliveryError
from app.core.db import SessionLocal, engine
from app.main import app
from app.models.feedback import FeedbackMessage


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_feedback.db"):
        os.remove("test_feedback.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_feedback.db"):
        os.remove("test_feedback.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_a_buyer_can_say_what_is_missing(client):
    response = client.post(
        "/feedback",
        json={
            "text": "Не хватает колонки со сроком поставки во вкладке Запросы",
            "origin": "requests",
        },
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["text"].startswith("Не хватает колонки")
    assert body["origin"] == "requests"
    assert body["author_name"] == "Иван Иванов"
    assert body["email_delivery_status"] == "disabled"


def test_feedback_sends_email_and_records_audit(client, monkeypatch):
    settings = SimpleNamespace(
        email_delivery_mode="live",
        email_from="app@example.com",
        feedback_email_to="owner@example.com",
    )
    monkeypatch.setattr(
        "app.services.feedback_notifications.effective_email_settings",
        lambda db: (settings, True, "environment"),
    )
    sent: list[dict] = []

    def fake_send(self, **kwargs):
        sent.append(kwargs)
        return kwargs["message_id"]

    monkeypatch.setattr(
        "app.services.feedback_notifications.EmailConnector.send", fake_send
    )

    response = client.post(
        "/feedback",
        json={"text": "Добавьте выгрузку отчёта", "origin": "requests"},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email_delivery_status"] == "sent"
    assert sent[0]["to_address"] == "owner@example.com"
    assert "Иван Иванов (ivanov)" in sent[0]["body"]
    assert "Добавьте выгрузку отчёта" in sent[0]["body"]
    assert sent[0]["message_id"].startswith("<feedback-")

    with SessionLocal() as db:
        stored = db.get(FeedbackMessage, body["id"])
        assert stored is not None
        assert stored.email_delivery_status == "sent"
        assert stored.email_message_id == sent[0]["message_id"]
        assert stored.email_delivery_attempted_at is not None


def test_feedback_survives_email_failure(client, monkeypatch):
    settings = SimpleNamespace(
        email_delivery_mode="live",
        email_from="app@example.com",
        feedback_email_to="owner@example.com",
    )
    monkeypatch.setattr(
        "app.services.feedback_notifications.effective_email_settings",
        lambda db: (settings, True, "environment"),
    )

    def fail_send(self, **kwargs):
        raise EmailDeliveryError("SMTP временно недоступен")

    monkeypatch.setattr(
        "app.services.feedback_notifications.EmailConnector.send", fail_send
    )

    response = client.post(
        "/feedback",
        json={"text": "Это обращение нельзя потерять"},
        headers=_auth(client, "ivanov"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email_delivery_status"] == "failed"
    listing = client.get("/feedback", headers=_auth(client, "ivanov")).json()
    assert any(item["id"] == body["id"] for item in listing)


def test_the_sender_sees_it_afterwards(client):
    """Иначе нажатие кнопки ничем не отличается от отправки в пустоту."""
    headers = _auth(client, "ivanov")
    client.post("/feedback", json={"text": "Непонятно, откуда балл"}, headers=headers)

    response = client.get("/feedback", headers=headers)

    assert response.status_code == 200
    assert any("Непонятно, откуда балл" == m["text"] for m in response.json())


def test_an_empty_message_is_refused(client):
    response = client.post(
        "/feedback", json={"text": "   "}, headers=_auth(client, "ivanov")
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Напишите, чего не хватает"


def test_a_buyer_does_not_read_other_people(client):
    """Обратная связь — не общий чат: чужое обращение не его дело."""
    client.post(
        "/feedback",
        json={"text": "Сообщение начальницы"},
        headers=_auth(client, "petrova"),
    )

    mine = client.get("/feedback", headers=_auth(client, "ivanov")).json()

    assert all(m["text"] != "Сообщение начальницы" for m in mine)


def test_the_head_and_the_admin_read_everything(client):
    """Ради этого раздел и заведён."""
    for username in ("petrova", "admin"):
        texts = [m["text"] for m in client.get("/feedback", headers=_auth(client, username)).json()]
        assert "Сообщение начальницы" in texts
        assert any(t.startswith("Не хватает колонки") for t in texts)


def test_the_newest_message_comes_first(client):
    """Разбирают такие сообщения сверху вниз и по свежести."""
    headers = _auth(client, "admin")
    client.post("/feedback", json={"text": "Самое свежее"}, headers=headers)

    listing = client.get("/feedback", headers=headers).json()

    assert listing[0]["text"] == "Самое свежее"


def test_an_auditor_may_write_too(client):
    """Аудитор читает программу внимательнее прочих и первым видит нехватку."""
    response = client.post(
        "/feedback",
        json={"text": "В журнале не видно, кто отменил запуск"},
        headers=_auth(client, "auditor"),
    )

    assert response.status_code == 201
