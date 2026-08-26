"""Загрузка списка позиций: права, ограничения и отсутствие следов файла.

Список сырья заказчика — коммерческая тайна: у него там 323 позиции с
поставщиками и объёмами. Эндпоинт обязан разобрать файл и забыть его, а не
складывать копии на диск и в журнал.
"""

import csv
import io
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_import_api.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.rfq_import import MAX_FILE_BYTES


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _token(client: TestClient, username: str) -> str:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


_SAMPLE = [
    ["Название", "CAS", "Объём", "Единица"],
    ["Бетаин", "107-43-7", "500", "кг"],
    ["Глицин", "56-40-6", "2", "т"],
]


def _upload(client: TestClient, token: str, payload: bytes, name: str = "list.csv"):
    return client.post(
        "/rfq/import/preview",
        files={"file": (name, payload, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_buyer_sees_every_parsed_row_before_anything_is_created(client):
    response = _upload(client, _token(client, "ivanov"), _csv_bytes(_SAMPLE))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_rows"] == 2
    assert body["importable_rows"] == 2
    assert [row["row"] for row in body["rows"]] == [2, 3]
    assert body["rows"][0]["values"]["volume"] == "500 kg"


def test_preview_creates_no_rfq(client):
    token = _token(client, "ivanov")
    before = client.get("/rfq", headers={"Authorization": f"Bearer {token}"}).json()
    _upload(client, token, _csv_bytes(_SAMPLE))
    after = client.get("/rfq", headers={"Authorization": f"Bearer {token}"}).json()
    # Предпросмотр обязан оставаться предпросмотром: создание — MEET2-02.
    assert len(after) == len(before)


def test_auditor_cannot_upload(client):
    response = _upload(client, _token(client, "auditor"), _csv_bytes(_SAMPLE))
    assert response.status_code == 403


def test_anonymous_cannot_upload(client):
    response = client.post(
        "/rfq/import/preview",
        files={"file": ("list.csv", _csv_bytes(_SAMPLE), "text/csv")},
    )
    assert response.status_code in {401, 403}


def test_oversized_file_is_refused_before_parsing(client):
    payload = b"x" * (MAX_FILE_BYTES + 1)
    response = _upload(client, _token(client, "ivanov"), payload)
    assert response.status_code == 413


def test_unsupported_format_is_refused_with_a_reason(client):
    response = _upload(
        client, _token(client, "ivanov"), b"%PDF-1.7", name="prices.pdf"
    )
    assert response.status_code == 422
    assert ".xlsx" in response.json()["detail"]


def test_broken_file_does_not_leak_its_contents(client, caplog):
    """В журнал не должно попасть ни строки из файла закупщика."""
    secret = "Ситагliptin от Хунань, 40 тонн, 12 USD/kg"
    payload = _csv_bytes([["Склад", "Артикул"], [secret, "SKU-1"]])

    with caplog.at_level(logging.DEBUG):
        response = _upload(client, _token(client, "ivanov"), payload)

    assert response.status_code == 422
    assert secret not in caplog.text
    assert secret not in response.text


def test_successful_upload_does_not_log_the_rows(client, caplog):
    with caplog.at_level(logging.DEBUG):
        response = _upload(client, _token(client, "ivanov"), _csv_bytes(_SAMPLE))

    assert response.status_code == 200
    assert "Бетаин" not in caplog.text


def test_one_bad_row_still_returns_the_good_ones(client):
    rows = [
        ["Название", "CAS"],
        ["Бетаин", "107-43-7"],
        ["Глицин", "56-40-7"],
    ]
    response = _upload(client, _token(client, "ivanov"), _csv_bytes(rows))
    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 2
    assert body["importable_rows"] == 1
    broken = body["rows"][1]
    assert broken["errors"][0]["row"] == 3
    assert broken["errors"][0]["field"] == "cas"
