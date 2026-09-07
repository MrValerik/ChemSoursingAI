"""Прогон поиска доводит продавца с площадки до канала связи.

Отдельно от разбора и от реестра намеренно: обе части по отдельности уже
проверены, а не работала как раз склейка. Так было с проверкой вещества по
реестру — код был написан, тест на функцию зелёный, а на прогоне вызов не
случался ни разу, потому что до него не доходило.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_seller_sites.db")

import pytest

from app.api.supplier_search import SupplierSearchRequest, execute_supplier_search
from app.connectors.web_page import FetchedPage
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, Supplier, User
from app.services.search_trace import create_search_run

from fastapi.testclient import TestClient

DB_FILE = "test_search_seller_sites.db"

ECHEMI_LISTING = {
    "title": "Aspirin CAS 50-78-2 - ECHEMI",
    "url": "https://www.echemi.com/produce/pr2112152898-aspirin.html",
    "snippet": (
        "Contact China Manufactory Shandong zhishang chemical Co.,Ltd for "
        "the product Aspirin CAS 50-78-2 . Chat now for more business."
    ),
}

OWN_SITE = {
    "title": "Zhishang Chemical",
    "url": "https://zhishangchem.com/",
    "snippet": "About us",
}


@pytest.fixture(scope="module")
def client():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)


def _mock_agents(monkeypatch):
    def response(self, **kwargs):
        if kwargs["schema_name"] == "substance_identity":
            return {
                "canonical_name": "2-acetyloxybenzoic acid",
                "search_names": ["Aspirin"],
                "input_name_matches": True,
                "substance_type": "single_substance",
                "ambiguities": [],
            }
        if kwargs["schema_name"] == "supplier_search_plan":
            return {
                "queries": [
                    {
                        "query": '"Aspirin" "50-78-2" manufacturer China',
                        "language": "en",
                        "purpose": "manufacturer",
                        "source_type": "official_site",
                        "priority": 1,
                    }
                ]
            }
        raise AssertionError(f"Unexpected schema: {kwargs['schema_name']}")

    monkeypatch.setattr("app.api.supplier_search.LLMClient.generate_json", response)


def _run(db, user):
    rfq = RFQ(cas="50-78-2", name="Aspirin", owner_id=user.id)
    db.add(rfq)
    db.flush()
    run = create_search_run(
        db,
        owner_id=user.id,
        rfq_id=rfq.id,
        input_payload={"cas": "50-78-2", "name": "Aspirin", "country": "Китай"},
    )
    db.commit()
    return run


def _request() -> SupplierSearchRequest:
    return SupplierSearchRequest(
        cas="50-78-2", name="Aspirin", country="Китай", limit=5
    )


def test_a_seller_named_by_the_platform_gets_a_contact(client, monkeypatch):
    _mock_agents(monkeypatch)
    monkeypatch.setattr(
        "app.api.supplier_search.search_web",
        lambda query, limit: [ECHEMI_LISTING],
    )
    # Поиск сайта — второй запрос, уже про компанию, а не про вещество.
    asked = []

    def site_search(query, limit=8):
        asked.append(query)
        return [OWN_SITE]

    monkeypatch.setattr("app.services.seller_site_lookup.search_web", site_search)
    monkeypatch.setattr(
        "app.services.seller_site_lookup.fetch_web_page",
        lambda url: FetchedPage(
            url=url,
            final_url=url,
            domain="zhishangchem.com",
            title="Zhishang Chemical",
            content_type="text/html",
            http_status=200,
            text="Email: sales@zhishangchem.com",
            content_hash="hash",
        ),
    )

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "ivanov").one()
        run = _run(db, user)
        execute_supplier_search(_request(), db, user, search_run=run)

        supplier = (
            db.query(Supplier)
            .filter(Supplier.company == "Shandong zhishang chemical Co.,Ltd")
            .one()
        )
        assert supplier.source == "https://zhishangchem.com/"
        assert supplier.contact_barrier is None
        managers = db.query(Manager).filter(Manager.supplier_id == supplier.id).all()
        assert [manager.email for manager in managers] == ["sales@zhishangchem.com"]

    assert asked == [
        "Shandong zhishang chemical Co.,Ltd official website contact email"
    ]


def test_a_seller_whose_site_is_not_found_is_not_asked_twice(client, monkeypatch):
    _mock_agents(monkeypatch)
    listing = {
        **ECHEMI_LISTING,
        "snippet": ECHEMI_LISTING["snippet"].replace(
            "Shandong zhishang chemical Co.,Ltd", "Hebei Guanlang Biotechnology Co.,Ltd"
        ),
    }
    monkeypatch.setattr(
        "app.api.supplier_search.search_web", lambda query, limit: [listing]
    )
    queries = []

    def site_search(query, limit=8):
        queries.append(query)
        # Имя компании стоит в заголовке чужой страницы — сайтом компании
        # она от этого не становится.
        return [{"url": "https://importgenius.cn/x", "title": "", "snippet": ""}]

    monkeypatch.setattr("app.services.seller_site_lookup.search_web", site_search)

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "ivanov").one()
        execute_supplier_search(_request(), db, user, search_run=_run(db, user))
        supplier = (
            db.query(Supplier)
            .filter(Supplier.company == "Hebei Guanlang Biotechnology Co.,Ltd")
            .one()
        )
        assert supplier.contact_barrier == "site_not_found"

        # Второй прогон по тому же продавцу запроса уже не тратит.
        execute_supplier_search(_request(), db, user, search_run=_run(db, user))

    assert len(queries) == 1
