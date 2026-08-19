"""Обратная связь: чего пользователю не хватает и что ему непонятно.

Раздел заведён не как служба поддержки, а как способ узнать словами
самого закупщика, чего в программе нет. Поэтому важны три вещи:
отправленное не пропадает, автор видит своё, а руководитель и
администратор — всё.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_feedback.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import engine
from app.main import app


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
