"""Пакетное создание запросов по списку позиций.

Пакет — это связь между независимыми запросами, а не запрос на несколько
веществ. Проверяется здесь прежде всего то, что список закупщика не
пропадает целиком из-за одной кривой строки и не задваивается из-за
повторного нажатия.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_batch.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models import RFQ, RfqBatch, SearchRun


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


def _headers(client: TestClient, username: str = "ivanov") -> dict:
    return {"Authorization": f"Bearer {_token(client, username)}"}


def _values(name: str, **kw) -> dict:
    base = {
        "name": name,
        "identification_method": "spec",
        "specification": "промышленный грейд",
        "incoterms": ["CIP"],
        "search_countries": ["Китай"],
    }
    base.update(kw)
    return base


def _post(client: TestClient, headers: dict, key: str, rows: list[dict], **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(
        f"/rfq/batch?{query}" if query else "/rfq/batch",
        json={
            "idempotency_key": key,
            "source_name": "list.csv",
            "items": [
                {"row": index, "values": values}
                for index, values in enumerate(rows, start=2)
            ],
        },
        headers=headers,
    )


def _cleanup(batch_id: int) -> None:
    """Прогон в очереди заберёт себе чужой worker, если его не убрать."""
    with SessionLocal() as db:
        rfq_ids = [
            row.id for row in db.query(RFQ).filter(RFQ.batch_id == batch_id).all()
        ]
        if rfq_ids:
            db.query(SearchRun).filter(SearchRun.rfq_id.in_(rfq_ids)).delete(
                synchronize_session=False
            )
            db.query(RFQ).filter(RFQ.id.in_(rfq_ids)).delete(
                synchronize_session=False
            )
        db.query(RfqBatch).filter(RfqBatch.id == batch_id).delete(
            synchronize_session=False
        )
        db.commit()


# --- штатное создание ---


def test_single_row_batch_creates_one_independent_rfq(client):
    headers = _headers(client)
    response = _post(client, headers, "batch-single-001", [_values("Бетаин")])
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["created"] is True
    assert body["total"] == 1
    assert body["created_count"] == 1
    assert body["failed_count"] == 0
    assert body["results"][0]["rfq_id"] is not None
    _cleanup(body["batch_id"])


def test_twenty_rows_create_twenty_linked_but_separate_rfqs(client):
    headers = _headers(client)
    rows = [_values(f"Вещество {i}") for i in range(20)]
    response = _post(client, headers, "batch-twenty-001", rows, start_search="false")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_count"] == 20

    with SessionLocal() as db:
        linked = db.query(RFQ).filter(RFQ.batch_id == body["batch_id"]).all()
        assert len(linked) == 20
        # Двадцать позиций — двадцать карточек, а не одна на всё вещество.
        assert len({rfq.id for rfq in linked}) == 20
        assert len({rfq.name for rfq in linked}) == 20
    _cleanup(body["batch_id"])


def test_fifty_rows_are_accepted(client):
    headers = _headers(client)
    rows = [_values(f"Позиция {i}") for i in range(50)]
    response = _post(client, headers, "batch-fifty-001", rows, start_search="false")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total"] == 50
    assert body["created_count"] == 50
    _cleanup(body["batch_id"])


# --- поиск по каждой позиции ---


def test_each_position_and_country_gets_its_own_run(client):
    headers = _headers(client)
    rows = [
        _values("Бетаин", search_countries=["Китай", "Индия"]),
        _values("Глицин", search_countries=["Китай"]),
    ]
    response = _post(client, headers, "batch-runs-001", rows)
    body = response.json()
    # Две страны у первой позиции и одна у второй — три отдельных прогона.
    assert body["search_runs"] == 3
    assert body["results"][0]["search_runs"] == 2
    assert body["results"][1]["search_runs"] == 1

    with SessionLocal() as db:
        rfq_ids = [
            row.id for row in db.query(RFQ).filter(RFQ.batch_id == body["batch_id"])
        ]
        runs = db.query(SearchRun).filter(SearchRun.rfq_id.in_(rfq_ids)).all()
        # У каждого прогона свой идентификатор — по нему и идёт трассировка.
        assert len({run.id for run in runs}) == 3
        assert {run.status for run in runs} == {"queued"}
        assert {run.input_payload["country"] for run in runs} == {"Китай", "Индия"}
    _cleanup(body["batch_id"])


def test_search_can_be_skipped(client):
    headers = _headers(client)
    response = _post(
        client, headers, "batch-norun-001", [_values("Бетаин")], start_search="false"
    )
    body = response.json()
    assert body["search_runs"] == 0
    _cleanup(body["batch_id"])


# --- частичный отказ ---


def test_one_bad_row_does_not_cancel_the_good_ones(client):
    headers = _headers(client)
    rows = [
        _values("Бетаин"),
        # Базис вне справочника: строка негодна, остальные — нет.
        _values("Глицин", incoterms=["DDP"]),
        _values("Мочевина"),
    ]
    response = _post(client, headers, "batch-partial-001", rows, start_search="false")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["total"] == 3
    assert body["created_count"] == 2
    assert body["failed_count"] == 1

    failed = [item for item in body["results"] if item["error"]]
    assert len(failed) == 1
    # Итог по каждой строке: номер строки и причина, а не общий отказ.
    assert failed[0]["row"] == 3
    assert failed[0]["name"] == "Глицин"
    assert "DDP" in failed[0]["error"]
    assert failed[0]["rfq_id"] is None

    with SessionLocal() as db:
        linked = db.query(RFQ).filter(RFQ.batch_id == body["batch_id"]).all()
        assert {rfq.name for rfq in linked} == {"Бетаин", "Мочевина"}
    _cleanup(body["batch_id"])


def test_row_without_a_name_fails_alone(client):
    headers = _headers(client)
    rows = [_values("Бетаин"), {"incoterms": ["CIP"], "search_countries": ["Китай"]}]
    response = _post(client, headers, "batch-noname-001", rows, start_search="false")
    body = response.json()
    assert body["created_count"] == 1
    assert body["failed_count"] == 1
    assert body["results"][1]["error"]
    _cleanup(body["batch_id"])


def test_bad_cas_row_fails_alone(client):
    headers = _headers(client)
    rows = [
        _values("Бетаин"),
        _values("Глицин", identification_method="cas", cas="56-40-7"),
    ]
    response = _post(client, headers, "batch-badcas-001", rows, start_search="false")
    body = response.json()
    assert body["created_count"] == 1
    assert body["failed_count"] == 1
    # Верная контрольная цифра названа — закупщику есть что исправить.
    assert "56-40-6" in body["results"][1]["error"]
    _cleanup(body["batch_id"])


# --- идемпотентность ---


def test_same_key_does_not_create_a_second_batch(client):
    headers = _headers(client)
    rows = [_values("Бетаин"), _values("Глицин")]
    first = _post(client, headers, "batch-idem-001", rows, start_search="false")
    second = _post(client, headers, "batch-idem-001", rows, start_search="false")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["batch_id"] == second.json()["batch_id"]
    # Повтор честно сообщает, что ничего не создавал.
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["created_count"] == 2

    with SessionLocal() as db:
        assert db.query(RFQ).filter(RFQ.batch_id == first.json()["batch_id"]).count() == 2
    _cleanup(first.json()["batch_id"])


def test_repeat_with_a_different_payload_still_returns_the_first_batch(client):
    """Ключ отвечает за действие, а не за содержимое.

    Повтор после обрыва ответа может уйти с чуть другим телом; создавать по
    нему второй набор запросов нельзя.
    """
    headers = _headers(client)
    first = _post(client, headers, "batch-idem-002", [_values("Бетаин")], start_search="false")
    second = _post(
        client, headers, "batch-idem-002", [_values("Совсем другое")], start_search="false"
    )
    assert first.json()["batch_id"] == second.json()["batch_id"]

    with SessionLocal() as db:
        names = {
            rfq.name
            for rfq in db.query(RFQ).filter(RFQ.batch_id == first.json()["batch_id"])
        }
        assert names == {"Бетаин"}
    _cleanup(first.json()["batch_id"])


def test_the_same_key_from_another_buyer_is_a_separate_batch(client):
    """Ключ уникален в пределах закупщика: чужой не должен блокировать."""
    rows = [_values("Бетаин")]
    mine = _post(client, _headers(client, "ivanov"), "batch-shared-key", rows, start_search="false")
    theirs = _post(client, _headers(client, "petrova"), "batch-shared-key", rows, start_search="false")
    assert mine.json()["batch_id"] != theirs.json()["batch_id"]
    assert theirs.json()["created"] is True
    _cleanup(mine.json()["batch_id"])
    _cleanup(theirs.json()["batch_id"])


# --- права ---


def test_auditor_cannot_create_a_batch(client):
    response = _post(
        client, _headers(client, "auditor"), "batch-rbac-001", [_values("Бетаин")]
    )
    assert response.status_code == 403


def test_batch_access_does_not_widen_access_to_other_rfqs(client):
    """Чужой пакет не выдаёт свои карточки и не подтверждает своё наличие."""
    mine = _post(
        client, _headers(client, "ivanov"), "batch-priv-001", [_values("Бетаин")],
        start_search="false",
    )
    batch_id = mine.json()["batch_id"]

    # Владелец видит сводку.
    own = client.get(f"/rfq/batch/{batch_id}", headers=_headers(client, "ivanov"))
    assert own.status_code == 200
    assert own.json()["total"] == 1

    # Руководитель видит все запросы — значит видит и пакет.
    head = client.get(f"/rfq/batch/{batch_id}", headers=_headers(client, "petrova"))
    assert head.status_code == 200
    _cleanup(batch_id)


def test_unknown_batch_is_not_found(client):
    response = client.get("/rfq/batch/99999", headers=_headers(client))
    assert response.status_code == 404


# --- сводка пакета ---


def test_batch_summary_lists_every_position_and_its_runs(client):
    headers = _headers(client)
    rows = [_values("Бетаин", volume="500 kg"), _values("Глицин")]
    created = _post(client, headers, "batch-summary-001", rows)
    batch_id = created.json()["batch_id"]

    response = client.get(f"/rfq/batch/{batch_id}", headers=headers)
    assert response.status_code == 200
    body = response.json()

    assert body["source_name"] == "list.csv"
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["Бетаин", "Глицин"]
    assert body["items"][0]["volume"] == "500 kg"
    # Из сводки можно открыть отдельный запрос — по его идентификатору.
    assert all(item["rfq_id"] for item in body["items"])
    assert body["items"][0]["search_runs"] == 1
    _cleanup(batch_id)


def test_empty_batch_is_refused(client):
    response = client.post(
        "/rfq/batch",
        json={"idempotency_key": "batch-empty-001", "items": []},
        headers=_headers(client),
    )
    assert response.status_code == 422


def test_short_idempotency_key_is_refused(client):
    response = client.post(
        "/rfq/batch",
        json={"idempotency_key": "short", "items": [{"row": 2, "values": _values("Бетаин")}]},
        headers=_headers(client),
    )
    assert response.status_code == 422


# --- умолчания списка ---


def _post_with_defaults(client, headers, key, rows, defaults):
    return client.post(
        "/rfq/batch?start_search=false",
        json={
            "idempotency_key": key,
            "source_name": "list.csv",
            "defaults": defaults,
            "items": [
                {"row": index, "values": values}
                for index, values in enumerate(rows, start=2)
            ],
        },
        headers=headers,
    )


def test_defaults_fill_what_the_file_does_not_have(client):
    """В файле закупщика колонок базиса и стран обычно нет.

    Без умолчаний весь список отвергался бы целиком: базис поставки в
    карточке запроса обязателен.
    """
    headers = _headers(client)
    rows = [
        {"name": "Бетаин", "identification_method": "spec", "specification": "тех."},
        {"name": "Глицин", "identification_method": "spec", "specification": "тех."},
    ]
    response = _post_with_defaults(
        client, headers, "batch-defaults-001", rows,
        {"incoterms": ["CIP", "FCA"], "search_countries": ["Китай"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_count"] == 2

    with SessionLocal() as db:
        created = db.query(RFQ).filter(RFQ.batch_id == body["batch_id"]).all()
        assert all(rfq.incoterms == ["CIP", "FCA"] for rfq in created)
        assert all(rfq.search_countries == ["Китай"] for rfq in created)
    _cleanup(body["batch_id"])


def test_row_value_beats_the_default(client):
    """Указанное в файле сильнее общего умолчания."""
    headers = _headers(client)
    rows = [
        {
            "name": "Бетаин",
            "identification_method": "spec",
            "specification": "тех.",
            "incoterms": ["FOB"],
            "search_countries": ["Индия"],
        },
        {"name": "Глицин", "identification_method": "spec", "specification": "тех."},
    ]
    response = _post_with_defaults(
        client, headers, "batch-defaults-002", rows,
        {"incoterms": ["CIP"], "search_countries": ["Китай"]},
    )
    body = response.json()
    assert body["created_count"] == 2

    with SessionLocal() as db:
        created = {
            rfq.name: rfq
            for rfq in db.query(RFQ).filter(RFQ.batch_id == body["batch_id"])
        }
        assert created["Бетаин"].incoterms == ["FOB"]
        assert created["Бетаин"].search_countries == ["Индия"]
        assert created["Глицин"].incoterms == ["CIP"]
        assert created["Глицин"].search_countries == ["Китай"]
    _cleanup(body["batch_id"])
