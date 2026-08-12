"""Род страницы вместо растущего списка регулярок.

За один день пришлось четырежды дописывать признаки негодных страниц:
заголовок, рекламная шапка, призыв купить, обзор рынка. И всё равно
прогон 281 завёл со страницы PubMed «компанию» с адресом
dtstuart@ualberta.ca — личной почтой исследователя из университета
Альберты, которому ушло бы коммерческое письмо. А агентство рыночных
отчётов «Market Research Intellect» стало дистрибьютором с баллом 45.

Замер по 23 сохранённым страницам с известным ответом: лёгкая модель,
которая и так вызывается по каждой странице, дала 21 верный ответ.
Ошибки только на границе «справочник против витрины»; научная статья,
обзор рынка и сайт компании определены верно во всех случаях. Большая
модель на том же наборе дала ровно столько же, так что платить за неё
незачем.

Поле только запрещает и никогда не доказывает: им можно отбросить
кандидата, но нельзя подтвердить роль. Ошибка в сторону запрета стоит
одного потерянного кандидата, ошибка в сторону доверия — письма не туда.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_page_kind.db")

import pytest
from fastapi.testclient import TestClient

from app.api.supplier_search import (
    NOT_A_SUPPLIER_PAGE,
    SupplierQualification,
    _apply_evidence_gates,
)
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, User
from app.services.search_trace import create_search_run
from app.services.supplier_registry import register_qualified_candidate


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_page_kind.db"):
        os.remove("test_page_kind.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_page_kind.db"):
        os.remove("test_page_kind.db")


def _claim(claim_type: str) -> dict:
    return {
        "claim_type": claim_type,
        "support_status": "supports",
        "quote_verified": True,
    }


def _qualification(**overrides) -> SupplierQualification:
    fields = {
        "result_index": 0,
        "company_name": "Некто",
        "title_ru": "Оценка",
        "summary_ru": "Описание",
        "supplier_type": "manufacturer",
        "cas_status": "confirmed",
        "country_status": "claimed",
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "confidence": 0,
        "red_flags": [],
        "missing_evidence": [],
        "evidence": [],
    }
    fields.update(overrides)
    return SupplierQualification(**fields)


# --- ворота ---


@pytest.mark.parametrize("kind", sorted(NOT_A_SUPPLIER_PAGE))
def test_a_page_that_is_not_a_company_loses_the_role(kind):
    payload = _apply_evidence_gates(
        _qualification(page_kind=kind),
        [_claim("chemical_identity"), _claim("production_capacity")],
    )

    assert payload["supplier_type"] == "unknown"
    assert any("не представляет компанию" in f for f in payload["red_flags"])


def test_the_reason_is_written_in_plain_words():
    """Закупщик должен понять отказ без чтения кода."""
    payload = _apply_evidence_gates(
        _qualification(page_kind="scientific"), [_claim("chemical_identity")]
    )
    assert any("научная публикация" in f for f in payload["red_flags"])


def test_a_company_site_keeps_its_proven_role():
    """Правило запрещает, но ничего не отнимает у доказанного."""
    payload = _apply_evidence_gates(
        _qualification(page_kind="company_site"),
        [_claim("chemical_identity"), _claim("production_capacity")],
    )
    assert payload["supplier_type"] == "manufacturer"


def test_a_storefront_is_still_a_company():
    """Магазин одной компании на площадке — это её страница."""
    payload = _apply_evidence_gates(
        _qualification(page_kind="marketplace_storefront"),
        [_claim("chemical_identity"), _claim("production_site")],
    )
    assert payload["supplier_type"] == "manufacturer"


def test_the_field_cannot_prove_anything():
    """company_site сам по себе роль не подтверждает."""
    payload = _apply_evidence_gates(
        _qualification(page_kind="company_site", supplier_type="manufacturer"),
        [_claim("chemical_identity")],
    )
    assert payload["supplier_type"] == "unknown"


# --- реестр ---


def test_such_a_page_registers_no_company_and_no_contact(client):
    """Ровно случай прогона 281: PubMed и почта исследователя."""
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas=None, name="C18-C22 fatty alcohol", owner_id=owner.id)
        db.add(rfq)
        db.flush()
        run = create_search_run(
            db,
            owner_id=owner.id,
            rfq_id=rfq.id,
            input_payload={"name": "C18-C22 fatty alcohol"},
        )
        before = db.query(Manager).count()

        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result={
                "result_index": 0,
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7744569/",
                "title": "Fatty alcohols biosynthesis",
                "company_name": "Не определено (научная статья)",
                "page_kind": "scientific",
                "supplier_type": "unknown",
                "confidence": 13,
                "gmp_status": "not_found",
                "iso_status": "not_found",
                "coa_status": "not_found",
                "tds_status": "not_found",
                "contacts": {"emails": ["dtstuart@ualberta.ca"]},
            },
        )
        db.commit()

        assert supplier is None
        assert db.query(Manager).count() == before
