"""Общий лимит обращений к домену: один на все worker-процессы."""

import os
import threading

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_domain_rate_limit.db")

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.connectors import pubchem as pubchem_module
from app.connectors import web_search as web_search_module
from app.connectors.pubchem import PubChemConnector
from app.connectors.web_search import search_web
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import DomainRateSlot
from app.services import domain_rate_limit
from app.services.domain_rate_limit import (
    DEFAULT_INTERVAL_S,
    defer_domain,
    interval_for,
    reserve_slot,
    reset_state,
    retry_after_seconds,
)


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_domain_rate_limit.db"):
        os.remove("test_domain_rate_limit.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_domain_rate_limit.db"):
        os.remove("test_domain_rate_limit.db")


@pytest.fixture(autouse=True)
def _clean_slots(client):
    reset_state()
    with SessionLocal() as db:
        for slot in db.query(DomainRateSlot).all():
            db.delete(slot)
        db.commit()
    yield


def test_shared_hosts_have_their_own_intervals():
    assert interval_for("html.duckduckgo.com") == 2.0
    assert interval_for("pubchem.ncbi.nlm.nih.gov") < 0.4, (
        "PubChem допускает пять запросов в секунду"
    )
    assert interval_for("supplier.example") == DEFAULT_INTERVAL_S


def test_slot_is_shared_between_independent_sessions(client):
    """Каждый вызов открывает свою сессию — как отдельный worker-процесс."""
    waits = [reserve_slot("https://shared.example/a", 0.5) for _ in range(4)]

    assert waits[0] == 0.0
    assert waits == sorted(waits), "очередь к домену должна расти монотонно"
    assert waits[1] == pytest.approx(0.5, abs=0.1)
    assert waits[2] == pytest.approx(1.0, abs=0.1)
    assert waits[3] == pytest.approx(1.5, abs=0.1)

    with SessionLocal() as db:
        assert db.get(DomainRateSlot, "shared.example") is not None


def test_parallel_workers_do_not_multiply_the_request_rate(client):
    """Четыре процесса не должны обращаться к домену вчетверо чаще."""
    interval = 0.4
    waits: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        wait = reserve_slot("https://busy.example/page", interval)
        with lock:
            waits.append(wait)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ordered = sorted(waits)
    assert len(ordered) == 4
    # Суммарный охват очереди — три интервала: четыре запроса не могут уйти
    # одновременно, как это было при паузе в памяти каждого процесса.
    assert ordered[-1] == pytest.approx(3 * interval, abs=0.15)
    assert all(
        later - earlier == pytest.approx(interval, abs=0.15)
        for earlier, later in zip(ordered, ordered[1:])
    )


def test_different_domains_do_not_queue_behind_each_other(client):
    assert reserve_slot("https://one.example/a", 5.0) == 0.0
    assert reserve_slot("https://two.example/a", 5.0) == 0.0


def test_retry_after_defers_the_domain_for_every_process(client):
    reserve_slot("https://limited.example/a", 0.1)
    defer_domain("https://limited.example/a", 5.0)

    wait = reserve_slot("https://limited.example/b", 0.1)
    assert wait == pytest.approx(5.0, abs=0.2), (
        "после Retry-After домен должен быть отложен для всех процессов"
    )


def test_retry_after_header_parsing():
    assert retry_after_seconds("30") == 30.0
    assert retry_after_seconds(None) == 0.0
    assert retry_after_seconds("") == 0.0
    # HTTP-date вместо секунд: точная дата не нужна, нужна безопасная пауза.
    assert retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") == DEFAULT_INTERVAL_S


def test_unavailable_database_falls_back_to_the_process_pause(client):
    """Отказ хранилища не должен останавливать поиск."""

    def broken_factory():
        raise OperationalError("SELECT 1", {}, Exception("нет соединения"))

    first = reserve_slot(
        "https://fallback.example/a", 0.3, session_factory=broken_factory
    )
    second = reserve_slot(
        "https://fallback.example/b", 0.3, session_factory=broken_factory
    )
    assert first == 0.0
    assert second == pytest.approx(0.3, abs=0.1), (
        "запасной путь обязан выдерживать паузу хотя бы внутри процесса"
    )
    assert domain_rate_limit._degraded is True


def test_search_web_defers_the_engine_after_a_throttled_response(
    client, monkeypatch
):
    """Выдача поисковика — общий для всех запусков адрес."""
    deferred: list[tuple[str, float]] = []
    monkeypatch.setattr(
        web_search_module,
        "defer_domain",
        lambda url, delay: deferred.append((url, delay)),
    )

    class _ThrottledClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url, params=None):
            request = httpx.Request("GET", url)
            return httpx.Response(
                429, headers={"Retry-After": "42"}, request=request, text=""
            )

    monkeypatch.setattr(web_search_module.httpx, "Client", _ThrottledClient)

    with pytest.raises(httpx.HTTPStatusError):
        search_web("aspirin manufacturer")

    assert deferred and deferred[0][1] == 42.0, (
        "Retry-After поисковика должен применяться ко всем процессам"
    )


def test_pubchem_requests_go_through_the_shared_limit(client, monkeypatch):
    """Каждый поиск обращается к PubChem трижды."""
    reserved: list[str] = []
    monkeypatch.setattr(
        pubchem_module,
        "reserve_slot",
        lambda url: reserved.append(url) or 0.0,
    )

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url):
            request = httpx.Request("GET", url)
            if "cids" in url:
                return httpx.Response(
                    200, json={"IdentifierList": {"CID": [2244]}}, request=request
                )
            if "property" in url:
                return httpx.Response(
                    200,
                    json={
                        "PropertyTable": {
                            "Properties": [
                                {
                                    "IUPACName": "aspirin",
                                    "MolecularFormula": "C9H8O4",
                                    "MolecularWeight": "180.16",
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"InformationList": {"Information": [{"Synonym": ["ASA"]}]}},
                request=request,
            )

    monkeypatch.setattr(pubchem_module.httpx, "Client", _Client)

    info = PubChemConnector().verify_cas("50-78-2")
    assert info.found is True
    assert len(reserved) == 3, (
        f"через лимит прошли не все запросы PubChem: {reserved}"
    )


def test_pubchem_not_found_is_not_treated_as_throttling(client, monkeypatch):
    """404 у PubChem означает «вещество не найдено», а не ограничение."""
    deferred: list[str] = []
    monkeypatch.setattr(
        pubchem_module,
        "defer_domain",
        lambda url, delay: deferred.append(url),
    )

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url):
            return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(pubchem_module.httpx, "Client", _Client)

    info = PubChemConnector().verify_cas("50-78-2")
    assert info.found is False
    assert info.error == "not_found"
    assert deferred == [], "404 не должен откладывать домен"
