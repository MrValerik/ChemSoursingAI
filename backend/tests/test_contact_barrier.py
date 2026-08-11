"""Почему связи нет — разное, и закупщику это надо знать.

«Нет контакта» читается как «компания недостижима», и строку вычёркивают.
На деле у Ningbo Inno адрес на странице есть, просто подменён Cloudflare
на «[email protected]»: написать можно, открыв страницу руками. А там,
где стоит только форма, адреса нет ни у кого, и путь один — заполнить её
на сайте, что может сделать лишь человек.

Проверено на шести сайтах из нашего реестра: cjspvc, keyingchem и
sprchemical публикуют адрес прямо, aogubiotech — на отдельной странице
контактов, echochemtech и nbinno подменяют его.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_contact_barrier.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, User
from app.services.contacts import (
    BARRIER_FORM,
    BARRIER_OBFUSCATED,
    find_contact_barrier,
)
from app.services.search_trace import create_search_run
from app.services.supplier_registry import register_qualified_candidate


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_contact_barrier.db"):
        os.remove("test_contact_barrier.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_contact_barrier.db"):
        os.remove("test_contact_barrier.db")


# --- чтение причины ---


def test_a_cloudflare_placeholder_is_recognised():
    """Ровно то, что стоит на странице Ningbo Inno."""
    text = "Email: [email protected] /cdn-cgi/l/email-protection"
    assert find_contact_barrier(text) == BARRIER_OBFUSCATED


def test_a_written_out_address_is_recognised():
    assert find_contact_barrier("write to sales (at) example dot cn") == (
        BARRIER_OBFUSCATED
    )


def test_an_inquiry_form_is_recognised():
    text = "Write your message here and send it to us. Send Inquiry"
    assert find_contact_barrier(text) == BARRIER_FORM


def test_a_chinese_form_is_recognised():
    assert find_contact_barrier("产品详情 在线留言 提交") == BARRIER_FORM


def test_a_hidden_address_outweighs_a_form():
    """Форма есть почти везде; скрытый адрес — более точная причина."""
    text = "Send Inquiry. Email: [email protected]"
    assert find_contact_barrier(text) == BARRIER_OBFUSCATED


def test_a_plain_page_has_no_barrier():
    assert find_contact_barrier("Adipic acid 99.7%, 25 kg bag") is None


# --- запись в реестр ---


def _run(db):
    owner = db.query(User).filter(User.username == "ivanov").one()
    rfq = RFQ(cas="124-04-9", name="Adipic acid", owner_id=owner.id)
    db.add(rfq)
    db.flush()
    return create_search_run(
        db,
        owner_id=owner.id,
        rfq_id=rfq.id,
        input_payload={"cas": "124-04-9", "name": "Adipic acid", "country": "Китай"},
    )


def _result(url: str, company: str, **overrides) -> dict:
    result = {
        "result_index": 0,
        "url": url,
        "title": company,
        "company_name": company,
        "supplier_type": "distributor",
        "confidence": 50,
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
    }
    result.update(overrides)
    return result


def test_the_reason_is_stored_when_there_is_no_contact(client):
    with SessionLocal() as db:
        supplier = register_qualified_candidate(
            db,
            search_run=_run(db),
            result=_result(
                "https://nbinno.example/x",
                "Нинбо Инно",
                contact_barrier=BARRIER_OBFUSCATED,
            ),
        )
        db.commit()

        assert supplier.contact_barrier == BARRIER_OBFUSCATED


def test_a_found_contact_clears_the_reason(client):
    """Нашли адрес — объяснять нечего."""
    with SessionLocal() as db:
        supplier = register_qualified_candidate(
            db,
            search_run=_run(db),
            result=_result(
                "https://keyingchem.example/x",
                "Кэйин Кемикал",
                contact_barrier=BARRIER_FORM,
                contacts={"emails": ["market@keyingchem.example"]},
            ),
        )
        db.commit()

        assert supplier.contact_barrier is None


def test_the_reason_reaches_the_table(client):
    with SessionLocal() as db:
        supplier = register_qualified_candidate(
            db,
            search_run=_run(db),
            result=_result(
                "https://formonly.example/x",
                "Только Форма",
                contact_barrier=BARRIER_FORM,
            ),
        )
        db.commit()
        supplier_id = supplier.id

    token = client.post(
        "/auth/login", json={"username": "ivanov", "password": "demo123"}
    ).json()["access_token"]
    response = client.get(
        "/suppliers", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json()}
    assert listed[supplier_id]["contact_barrier"] == BARRIER_FORM
    assert listed[supplier_id]["channels"] == []
