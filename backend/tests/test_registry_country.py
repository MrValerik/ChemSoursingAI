"""Реестр как источник поиска: доказанная страна и вопрос к своим же.

Раньше ``suppliers.country`` заполнялся страной из формы поиска: «Китай»
означало «найдено, когда искали по Китаю», а не «компания находится в
Китае». Замер на боевой базе: 279 записей, у всех страна проставлена, и
253 из них «Китай» просто потому, что столько было поисков по Китаю.
Фильтровать поиск по такому полю значит подтверждать собственное
допущение, поэтому рядом хранится то, чем связь подтверждена.

Модуль держит собственную базу. Остальной набор работает на одной общей,
и заведённые здесь компании считали бы чужие тесты: реестр — ровно та
таблица, размер которой они проверяют.
"""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.supplier_search import _known_supplier_plan_items
from app.models import SearchRun, Supplier, User
from app.models.base import Base
from app.models.enums import SupplierType, UserRole
from app.services.supplier_registry import register_qualified_candidate

_DB_PATH = "test_registry_country.db"


@pytest.fixture(scope="module")
def session_factory():
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)
    engine = create_engine(f"sqlite:///./{_DB_PATH}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def owner(db):
    record = db.scalar(select(User).limit(1))
    if record is None:
        record = User(
            username="tester",
            full_name="Тестовый закупщик",
            password_hash="x",
            role=UserRole.BUYER,
        )
        db.add(record)
        db.commit()
    return record


def _result(**overrides) -> dict:
    base = {
        "url": "https://factory.example/product",
        "company_name": "Example Chemical Co., Ltd",
        "supplier_type": "manufacturer",
        "country_status": "claimed",
        "confidence": 55,
        "page_kind": "company_site",
        "evidence": [
            {
                "claim_type": "country",
                "quote": "Our plant is located in Shandong, China",
                "quote_verified": True,
            }
        ],
    }
    base.update(overrides)
    return base


def _run(db, owner, **payload) -> SearchRun:
    run = SearchRun(
        correlation_id=f"test-{payload.get('tag', 'run')}",
        owner_id=owner.id,
        input_payload={"name": "Betaine", "country": "Китай", **payload},
        status="completed",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    return run


# --- доказанная страна ---


def test_the_country_is_kept_with_the_quote_it_rests_on(db, owner):
    run = _run(db, owner, tag="quote")
    supplier = register_qualified_candidate(db, search_run=run, result=_result())
    db.commit()

    assert supplier is not None
    assert supplier.country == "Китай"
    assert supplier.country_status == "claimed"
    assert "Shandong" in supplier.country_evidence


def test_a_page_naming_another_country_records_no_country(db, owner):
    """При mismatch страница прямо назвала другую страну."""
    run = _run(db, owner, tag="mismatch")
    supplier = register_qualified_candidate(
        db,
        search_run=run,
        result=_result(
            url="https://german.example/p",
            company_name="German Chem GmbH",
            country_status="mismatch",
            evidence=[],
        ),
    )
    db.commit()

    assert supplier.country is None
    assert supplier.country_status is None


def test_a_weaker_confirmation_does_not_overwrite_a_stronger_one(db, owner):
    """Один неудачный источник не отменяет уже прочитанной цитаты."""
    run = _run(db, owner, tag="rank")
    first = register_qualified_candidate(
        db,
        search_run=run,
        result=_result(url="https://two.example/a", company_name="Two Chem"),
    )
    db.commit()
    assert first.country_status == "claimed"

    again = register_qualified_candidate(
        db,
        search_run=run,
        result=_result(
            url="https://two.example/b",
            company_name="Two Chem",
            country_status="not_found",
            evidence=[],
        ),
    )
    db.commit()

    assert again.id == first.id
    assert again.country_status == "claimed"
    assert "Shandong" in again.country_evidence


# --- реестр пополняется без заявки ---


def test_a_run_without_an_rfq_still_fills_the_registry(db, owner):
    """Свободный поиск раньше не заводил в реестр ничего."""
    run = _run(db, owner, tag="norfq")
    assert run.rfq_id is None

    supplier = register_qualified_candidate(
        db,
        search_run=run,
        result=_result(url="https://free.example/p", company_name="Free Search Chem"),
    )
    db.commit()

    assert supplier is not None
    assert supplier.qualification_status == "candidate"


# --- вопрос к известным компаниям ---


def test_known_manufacturers_are_asked_in_one_query(db):
    """Поисковик считает кредит за запрос, а не за имя в нём."""
    for index in range(3):
        db.add(
            Supplier(
                company=f"Known Factory {index}",
                company_key=f"knownfactory{index}",
                country="Китай",
                country_status="claimed",
                type=SupplierType.MANUFACTURER,
                evidence_score=50 + index,
                qualification_status="candidate",
            )
        )
    db.commit()

    plan = _known_supplier_plan_items(db, country="Китай", subject='"107-43-7"')

    assert len(plan) == 1
    assert plan[0].query.count(" OR ") >= 2
    assert '"107-43-7"' in plan[0].query


def test_a_company_whose_role_is_not_yet_known_is_still_asked(db):
    """Вопрос к реестру звучит «делаете ли вы ещё и это».

    Замер на боевом прогоне 318: страна доказана у всех пяти заведённых
    компаний, а роль установлена у одной. Требование роли оставило бы
    волну без участников, а роль всё равно оценивается заново по той
    странице, которую вернёт ответ.
    """
    for index in range(2):
        db.add(
            Supplier(
                company=f"Unknown Role Factory {index}",
                company_key=f"unknownrolefactory{index}",
                country="Вьетнам",
                country_status="claimed",
                type=None,
                qualification_status="candidate",
            )
        )
    db.commit()

    plan = _known_supplier_plan_items(db, country="Вьетнам", subject='"107-43-7"')

    assert len(plan) == 1


def test_a_known_trading_company_is_not_asked(db):
    """Её роль уже установлена, и она не та."""
    for index in range(2):
        db.add(
            Supplier(
                company=f"Trading House {index}",
                company_key=f"tradinghouse{index}",
                country="Турция",
                country_status="claimed",
                type=SupplierType.DISTRIBUTOR,
                qualification_status="candidate",
            )
        )
    db.commit()

    assert _known_supplier_plan_items(db, country="Турция", subject='"107-43-7"') == []


def test_a_company_without_a_proven_country_is_not_asked(db):
    """Иначе поиск подтверждал бы собственное допущение."""
    db.add(
        Supplier(
            company="Assumed Factory",
            company_key="assumedfactory",
            country="Индия",
            country_status=None,
            type=SupplierType.MANUFACTURER,
            qualification_status="candidate",
        )
    )
    db.commit()

    assert _known_supplier_plan_items(db, country="Индия", subject='"107-43-7"') == []


def test_one_company_is_not_a_registry_check(db):
    db.add(
        Supplier(
            company="Lonely Factory",
            company_key="lonelyfactory",
            country="Россия",
            country_status="claimed",
            type=SupplierType.MANUFACTURER,
            qualification_status="candidate",
        )
    )
    db.commit()

    assert _known_supplier_plan_items(db, country="Россия", subject='"107-43-7"') == []


def test_nothing_is_asked_without_a_subject(db):
    assert _known_supplier_plan_items(db, country="Китай", subject="  ") == []
