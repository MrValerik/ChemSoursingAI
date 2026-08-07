"""Имя завода со страницы дистрибьютора становится кандидатом.

Замер по эталону: из девяти известных поставщиков адипиновой кислоты в
Китае система находила трёх. Shenma, Hualu Hengsheng и Ляоянский НПЗ —
три ненайденных, которые держат рынок, — перечислены на странице
дистрибьютора Shandong Aojin среди марок, которые он перепродаёт. Эту
страницу мы загружаем и читаем каждый прогон.

В сниппет выдачи перечень не попадает: он в теле страницы. Поэтому
догонять имена надо там, где страницы уже прочитаны, а не в поиске.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_mined_candidates.db")

import pytest
from fastapi.testclient import TestClient

from app.api.supplier_search import (
    SupplierSearchResultInput,
    _producer_names_to_chase,
)
from app.connectors.web_page import FetchedPage
from app.core.db import engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_mined_candidates.db"):
        os.remove("test_mined_candidates.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_mined_candidates.db"):
        os.remove("test_mined_candidates.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _candidate(title: str, url: str) -> SupplierSearchResultInput:
    return SupplierSearchResultInput(title=title, url=url, snippet="")


# --- отбор имён ---


def test_a_name_from_the_page_is_chased():
    sources = [
        {
            "page_text": (
                "Adipic acid. Brands we supply: Hualu Hengsheng Chemical "
                "Co., Ltd and Shenma Industry Co., Ltd."
            )
        }
    ]
    names = _producer_names_to_chase(
        sources,
        [_candidate("Shandong Aojin", "https://aojinchem.com/adipic")],
        subject_names=["Adipic acid"],
    )
    assert any("Hualu" in name for name in names)


def test_a_company_already_among_candidates_is_not_chased():
    sources = [{"page_text": "Supplied by Hualu Hengsheng Chemical Co., Ltd."}]
    names = _producer_names_to_chase(
        sources,
        [_candidate("Hualu Hengsheng Chemical Co., Ltd", "https://hlhs.cn/a")],
        subject_names=["Adipic acid"],
    )
    assert names == []


def test_the_substance_is_not_chased_as_a_company():
    sources = [{"page_text": "Adipic Acid Chemical Co., Ltd product page"}]
    names = _producer_names_to_chase(
        sources,
        [_candidate("Seller", "https://seller.cn/a")],
        subject_names=["Adipic Acid Chemical"],
    )
    assert names == []


def test_nothing_is_chased_without_page_text():
    assert _producer_names_to_chase([], [], subject_names=["Adipic acid"]) == []


def test_the_number_of_chased_names_is_bounded():
    text = " ".join(f"Company Number{n} Chemical Co., Ltd." for n in range(8))
    names = _producer_names_to_chase(
        [{"page_text": text}], [], subject_names=["Adipic acid"]
    )
    assert len(names) <= 2


# --- заход целиком ---


def test_the_mined_company_becomes_a_qualified_candidate(client, monkeypatch):
    buyer = _auth(client, "ivanov")
    seen_queries: list[str] = []

    def fake_search(query, limit):
        seen_queries.append(query)
        return [
            {
                "title": "Hualu Hengsheng official",
                "url": "https://hlhs.example/adipic-acid",
                "snippet": "Adipic acid producer",
            }
        ]

    def fake_fetch(url):
        if "hlhs" in url:
            text = (
                "Shandong Hualu Hengsheng. Our own plant produces adipic acid "
                "CAS 124-04-9. 年产 320000 吨"
            )
        else:
            text = (
                "Adipic acid trading. Brands we supply: Hualu Hengsheng "
                "Chemical Co., Ltd. CAS 124-04-9."
            )
        return FetchedPage(
            url=url,
            final_url=url,
            domain="example",
            title="Adipic acid",
            content_type="text/html",
            http_status=200,
            text=text,
            content_hash="c" * 64,
        )

    monkeypatch.setattr("app.api.supplier_search.search_web", fake_search)
    monkeypatch.setattr("app.api.supplier_search.fetch_web_page", fake_fetch)
    monkeypatch.setattr(
        "app.api.supplier_search.LLMClient.generate_json",
        lambda self, **kwargs: {"results": []},
    )

    response = client.post(
        "/supplier-search/qualify",
        headers=buyer,
        json={
            "cas": "124-04-9",
            "name": "Adipic acid",
            "country": "Китай",
            "target_count": 1,
            "results": [
                {
                    "title": "Shandong Aojin trading",
                    "url": "https://aojin.example/adipic-acid",
                    "snippet": "Adipic acid supplier",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    urls = [str(item.get("url")) for item in payload["results"]]

    assert any("hlhs.example" in url for url in urls), urls
    assert any("Hualu" in query for query in seen_queries), seen_queries
