"""Троттлинг PubChem — состояние на секунды, а не свойство вещества.

Замер на списке заказчика: три запроса, заведённые подряд, дали два
«сервис недоступен». Те же вещества, проверенные по одному с паузой,
подтвердились сразу. Закупщик заводит запросы списком — у него в файле 323
позиции, — и первые три выбирают лимит на всех остальных.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_pubchem_throttling.db")

import httpx
import pytest

from app.connectors import pubchem
from app.connectors.pubchem import PubChemConnector


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Тест проверяет логику повтора, а не умение ждать."""
    monkeypatch.setattr(pubchem, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pubchem, "reserve_slot", lambda _url: 0.0)
    monkeypatch.setattr(pubchem, "defer_domain", lambda *a, **kw: None)


def _client(responses: list[int]) -> httpx.Client:
    """Клиент, отдающий заранее заданную последовательность кодов."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        status = responses[index]
        if status != 200:
            return httpx.Response(status, text="throttled")
        if "/cids/" in str(request.url):
            body = {"IdentifierList": {"CID": [7847]}}
        elif "/property/" in str(request.url):
            body = {
                "PropertyTable": {
                    "Properties": [
                        {
                            "IUPACName": "hexanedioic acid",
                            "MolecularFormula": "C6H10O4",
                            "MolecularWeight": "146.14",
                        }
                    ]
                }
            }
        else:
            body = {"InformationList": {"Information": [{"Synonym": ["Adipic acid"]}]}}
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_throttled_answer_is_retried(monkeypatch):
    """Первый ответ 503, второй — нормальный: вещество должно подтвердиться."""
    client = _client([503, 200])
    monkeypatch.setattr(pubchem.httpx, "Client", lambda **kw: client)

    info = PubChemConnector().verify_cas("124-04-9")

    assert info.found is True
    assert info.outcome == "confirmed"


def test_persistent_throttling_is_reported_as_unavailable(monkeypatch):
    """Если сервис не отвечает и после повтора, это факт о нас, не о веществе."""
    client = _client([503])
    monkeypatch.setattr(pubchem.httpx, "Client", lambda **kw: client)

    info = PubChemConnector().verify_cas("124-04-9")

    assert info.found is False
    assert info.outcome == "unavailable"


def test_a_missing_substance_is_not_retried_as_throttling(monkeypatch):
    """404 — это ответ по существу, повторять его незачем."""
    client = _client([404])
    monkeypatch.setattr(pubchem.httpx, "Client", lambda **kw: client)

    info = PubChemConnector().verify_cas("8013-07-8")

    assert info.outcome == "not_found"


def test_a_bad_checksum_never_reaches_the_network(monkeypatch):
    def explode(**kw):
        raise AssertionError("сеть не должна вызываться")

    monkeypatch.setattr(pubchem.httpx, "Client", explode)
    assert PubChemConnector().verify_cas("107-43-8").outcome == "invalid_checksum"
