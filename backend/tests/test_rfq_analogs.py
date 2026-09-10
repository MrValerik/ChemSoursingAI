"""Двухступенчатый подбор аналогов через API.

Смысл ступени: запрос «нужна замена» не идёт к поставщикам сам. Сначала
система называет вещества-заменители с доказательствами, закупщик отмечает
подходящие, и только после этого на каждое отмеченное заводится свой запрос
со своим поиском. Проверяется именно это: что поиск не стартует раньше
выбора и что выбор превращается в отдельные запросы, а не в один общий.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq_analogs.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models import RFQ, RfqAnalogCandidate, RfqBatch, SearchRun
from app.services import analog_candidates
from app.services.analog_candidates import AnalogCandidate, AnalogSuggestion


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _headers(client: TestClient, username: str = "ivanov") -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _stub_suggestion(monkeypatch, *candidates: AnalogCandidate, warnings=()) -> None:
    """Подменяет сетевую ступень: тест проверяет поток, а не выдачу."""

    def _suggest(name, *, cas=None, specification=None, llm=None):
        return AnalogSuggestion(
            query=name,
            candidates=list(candidates),
            warnings=list(warnings),
            search_used=True,
            llm_used=True,
        )

    monkeypatch.setattr("app.api.rfq.suggest_analogs", _suggest)
    monkeypatch.setattr(analog_candidates, "suggest_analogs", _suggest, raising=False)


def _create_analog_rfq(client: TestClient, headers: dict, name: str) -> int:
    response = client.post(
        "/rfq?verify=false&start_search=true",
        json={
            "identification_method": "analog",
            "name": name,
            "specification": "загуститель для холодного процесса",
            "incoterms": ["CIP"],
            "search_countries": ["Китай"],
        },
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _cleanup(rfq_id: int) -> None:
    """Убирает запрос, его детей и их прогоны: очередь общая с боевым worker."""
    with SessionLocal() as db:
        children = [
            row.created_rfq_id
            for row in db.query(RfqAnalogCandidate)
            .filter(RfqAnalogCandidate.rfq_id == rfq_id)
            .all()
            if row.created_rfq_id is not None
        ]
        batch_ids = [
            row.batch_id
            for row in db.query(RFQ).filter(RFQ.id.in_(children or [0])).all()
            if row.batch_id is not None
        ]
        ids = [rfq_id, *children]
        db.query(SearchRun).filter(SearchRun.rfq_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(RfqAnalogCandidate).filter(
            RfqAnalogCandidate.rfq_id == rfq_id
        ).delete(synchronize_session=False)
        db.query(RFQ).filter(RFQ.id.in_(ids)).delete(synchronize_session=False)
        if batch_ids:
            db.query(RfqBatch).filter(RfqBatch.id.in_(batch_ids)).delete(
                synchronize_session=False
            )
        db.commit()


def test_analog_request_does_not_start_a_supplier_search(client):
    """Главное правило ступени: пока замена не выбрана, искать компании не по чему."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")

    with SessionLocal() as db:
        runs = db.query(SearchRun).filter(SearchRun.rfq_id == rfq_id).count()
    assert runs == 0

    _cleanup(rfq_id)


def test_suggestion_is_stored_and_read_back(client, monkeypatch):
    """Подбор кладётся рядом с запросом и переживает перезагрузку страницы."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")
    _stub_suggestion(
        monkeypatch,
        AnalogCandidate(
            name="Гуаровая камедь",
            cas="9000-30-0",
            cas_confirmed=True,
            reason="Тот же загуститель, другое сырьё.",
            quote="guar gum replaces xanthan gum",
            source_url="https://example.test/thickeners",
        ),
        warnings=["Часть выдачи не получена."],
    )

    posted = client.post(f"/rfq/{rfq_id}/analogs/suggest", headers=headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["suggested_at"] is not None
    assert body["warnings"] == ["Часть выдачи не получена."]
    assert [item["name"] for item in body["candidates"]] == ["Гуаровая камедь"]
    assert body["candidates"][0]["cas_confirmed"] is True

    read = client.get(f"/rfq/{rfq_id}/analogs", headers=headers)
    assert read.status_code == 200
    assert read.json()["candidates"] == body["candidates"]

    _cleanup(rfq_id)


def test_confirmed_analogs_become_separate_requests_with_their_own_searches(
    client, monkeypatch
):
    """Выбор превращается в отдельный запрос на каждое вещество, а не в один общий."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")
    _stub_suggestion(
        monkeypatch,
        AnalogCandidate(
            name="Гуаровая камедь",
            cas="9000-30-0",
            cas_confirmed=True,
            reason="Тот же загуститель.",
            quote="guar gum",
            source_url="https://example.test/1",
        ),
        AnalogCandidate(
            name="Камедь рожкового дерева",
            reason="Тот же класс, другое сырьё.",
            quote="locust bean gum",
            source_url="https://example.test/2",
        ),
    )
    suggested = client.post(f"/rfq/{rfq_id}/analogs/suggest", headers=headers)
    candidates = suggested.json()["candidates"]

    confirmed = client.post(
        f"/rfq/{rfq_id}/analogs/confirm",
        json={"candidate_ids": [item["id"] for item in candidates]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    batch = confirmed.json()["batch"]
    assert batch is not None
    assert batch["created_count"] == 2
    # По одному поиску на позицию и страну: страна у родителя одна.
    assert batch["search_runs"] == 2

    with SessionLocal() as db:
        children = (
            db.query(RFQ).filter(RFQ.batch_id == batch["batch_id"]).order_by(RFQ.id).all()
        )
        assert [child.name for child in children] == [
            "Гуаровая камедь",
            "Камедь рожкового дерева",
        ]
        # Подтверждённый номер уходит в запрос, неподтверждённого нет —
        # тогда запрос идёт спецификацией, как обычная позиция без CAS.
        assert children[0].identification_method == "cas"
        assert children[0].cas == "9000-30-0"
        assert children[1].identification_method == "spec"
        assert children[1].cas is None
        # Условия закупки наследуются от исходной позиции.
        assert children[0].incoterms == ["CIP"]
        assert children[0].search_countries == ["Китай"]
        # Откуда взялся запрос, видно из карточки, а не только из журнала.
        assert f"запрос №{rfq_id}" in (children[0].specialist_comment or "")

    _cleanup(rfq_id)


def test_repeating_the_same_choice_does_not_create_a_second_set(client, monkeypatch):
    """Повторное нажатие не заводит второй набор запросов."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")
    _stub_suggestion(
        monkeypatch,
        AnalogCandidate(
            name="Гуаровая камедь",
            reason="Тот же загуститель.",
            quote="guar gum",
            source_url="https://example.test/1",
        ),
    )
    candidates = client.post(
        f"/rfq/{rfq_id}/analogs/suggest", headers=headers
    ).json()["candidates"]
    chosen = {"candidate_ids": [candidates[0]["id"]]}

    first = client.post(
        f"/rfq/{rfq_id}/analogs/confirm", json=chosen, headers=headers
    ).json()
    second = client.post(
        f"/rfq/{rfq_id}/analogs/confirm", json=chosen, headers=headers
    ).json()

    assert first["batch"]["created_count"] == 1
    # Второй раз создавать нечего: запрос по этому аналогу уже заведён.
    assert second["batch"] is None
    with SessionLocal() as db:
        assert (
            db.query(RFQ).filter(RFQ.name == "Гуаровая камедь").count() == 1
        )

    _cleanup(rfq_id)


def test_repeated_suggestion_keeps_analogs_that_already_became_requests(
    client, monkeypatch
):
    """Повторный подбор не стирает объяснение, откуда взялся заведённый запрос."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")
    _stub_suggestion(
        monkeypatch,
        AnalogCandidate(
            name="Гуаровая камедь",
            reason="Тот же загуститель.",
            quote="guar gum",
            source_url="https://example.test/1",
        ),
    )
    candidates = client.post(
        f"/rfq/{rfq_id}/analogs/suggest", headers=headers
    ).json()["candidates"]
    client.post(
        f"/rfq/{rfq_id}/analogs/confirm",
        json={"candidate_ids": [candidates[0]["id"]]},
        headers=headers,
    )

    _stub_suggestion(
        monkeypatch,
        AnalogCandidate(
            name="Камедь тары",
            reason="Тот же класс.",
            quote="tara gum",
            source_url="https://example.test/3",
        ),
    )
    again = client.post(f"/rfq/{rfq_id}/analogs/suggest", headers=headers).json()

    names = [item["name"] for item in again["candidates"]]
    assert "Гуаровая камедь" in names
    assert "Камедь тары" in names

    _cleanup(rfq_id)


def test_auditor_cannot_start_a_suggestion(client):
    """Аудитор — только чтение: подбор тратит поиск и заводит записи."""
    headers = _headers(client)
    rfq_id = _create_analog_rfq(client, headers, "Ксантановая камедь")

    auditor = _headers(client, "auditor")
    assert (
        client.post(f"/rfq/{rfq_id}/analogs/suggest", headers=auditor).status_code
        == 403
    )
    assert (
        client.post(
            f"/rfq/{rfq_id}/analogs/confirm",
            json={"candidate_ids": []},
            headers=auditor,
        ).status_code
        == 403
    )

    _cleanup(rfq_id)
