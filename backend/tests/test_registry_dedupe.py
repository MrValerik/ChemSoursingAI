"""Одна компания — одна строка в таблице отбора.

Дедупликация шла по адресу страницы, поэтому компания, найденная на своём
сайте и на двух площадках, давала три записи: по реестру 182 поставщика
при 159 различных компаниях — 23 лишние строки. Hangzhou Leap Chem лежала
четырьмя, Jiangsu Honon и TNJ Chemical — тремя, и контакт садился только
на одну из них, так что остальные строки в таблице были бесполезны.

Здесь же — правило про обзор рынка: отчёт «potassium sorbate market»
перечислял ведущих игроков, модель взяла оттуда имя Henan GP Chemicals, а
контакты снялись со страницы, и в реестре появился «Henan GP» с почтой
исследовательского агентства.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_registry_dedupe.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, Supplier, User
from app.services.page_facts import looks_like_market_report
from app.services.search_trace import create_search_run
from app.services.supplier_registry import company_key, register_qualified_candidate


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_registry_dedupe.db"):
        os.remove("test_registry_dedupe.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_registry_dedupe.db"):
        os.remove("test_registry_dedupe.db")


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
        "supplier_type": "manufacturer",
        "confidence": 60,
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
    }
    result.update(overrides)
    return result


# --- ключ компании ---


def test_legal_tails_and_punctuation_do_not_split_a_company():
    assert company_key("JiangSu Honon Silicon Co., Ltd.") == company_key(
        "Jiangsu Honon Silicon Co Ltd"
    )
    assert company_key("Hangzhou Leap Chem Co.,Ltd") == company_key(
        "Hangzhou  Leap-Chem"
    )


def test_a_short_name_gets_no_key():
    """Короткое имя после нормализации совпадает случайно."""
    assert company_key("ABC") is None


def test_a_placeholder_name_gets_no_key():
    """Две нераспознанные компании — это две компании, а не одна.

    В реестре нашлись две записи «Неизвестно»: по имени они слились бы в
    одну строку и утащили бы с собой чужие связи с запросами.
    """
    assert company_key("Неизвестно") is None
    assert company_key("Не указано") is None
    assert company_key("Unknown") is None
    assert company_key("Поставщик") is None


# --- схлопывание ---


def test_the_same_company_on_three_pages_is_one_row(client):
    with SessionLocal() as db:
        run = _run(db)
        pages = [
            ("http://www.jshonon.cn/contact.html", "Jiangsu Honon Silicon Co., Ltd"),
            ("https://jshonon.en.made-in-china.com/", "JiangSu Honon Silicon Co., Ltd."),
            ("https://jshonon.sell.everychina.com/x.html", "JiangSu Honon Silicon"),
        ]
        ids = {
            register_qualified_candidate(
                db, search_run=run, result=_result(url, company)
            ).id
            for url, company in pages
        }
        db.commit()

        assert len(ids) == 1


def test_contacts_from_different_pages_land_on_one_company(client):
    with SessionLocal() as db:
        run = _run(db)
        first = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://leapchem.example/a",
                "Hangzhou Leap Chem Co., Ltd",
                contacts={"emails": ["sales@leapchem.example"]},
            ),
        )
        second = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://leapchem.example/b",
                "Hangzhou LeapChem",
                contacts={"emails": ["info@leapchem.example"]},
            ),
        )
        db.commit()

        assert first.id == second.id
        emails = {
            m.email
            for m in db.query(Manager).filter(Manager.supplier_id == first.id).all()
        }
        assert emails == {"sales@leapchem.example", "info@leapchem.example"}


def test_a_different_company_is_not_merged(client):
    with SessionLocal() as db:
        run = _run(db)
        one = register_qualified_candidate(
            db, search_run=run, result=_result("https://a.example/x", "Шаньдун Аоцзинь")
        )
        two = register_qualified_candidate(
            db, search_run=run, result=_result("https://b.example/x", "Хэнань Джи Пи")
        )
        db.commit()

        assert one.id != two.id


# --- обзор рынка ---


def test_a_market_report_is_recognised():
    text = (
        "The potassium sorbate market size was valued at USD 1.2 billion. "
        "Key players include Henan GP Chemicals. CAGR of 4.1% over the "
        "forecast period."
    )
    assert looks_like_market_report(
        "https://straitsresearch.com/report/potassium-sorbate-market", text
    )


def test_a_supplier_page_with_the_word_market_is_not_a_report():
    """У живого завода такие обороты тоже встречаются.

    У Shandong Xinjiangye, который стоит в эталоне производителем, их
    шесть — поэтому одних оборотов мало, нужен ещё раздел статей.
    """
    text = "Our products serve the global market. Market share is growing."
    assert not looks_like_market_report("https://xinjiangyechemical.com/", text)


def test_a_market_report_never_lends_its_own_mailbox(client):
    """Компанию из отчёта показываем, почту отчёта — никогда.

    Раньше такая карточка отбрасывалась целиком, но закупщик по запросу
    #37 заметил, что найденных компаний больше, чем отобранных, и
    справедливо попросил не прятать находки. Опасна тут не компания, а
    адрес: sales@straitsresearch.com принадлежит агентству, и однажды он
    уже сел на живую строку Henan GP.
    """
    with SessionLocal() as db:
        run = _run(db)
        before = db.query(Manager).count()
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://straitsresearch.com/report/potassium-sorbate-market",
                "Henan GP Chemicals Co., Ltd",
                is_market_report=True,
                contacts={"emails": ["sales@straitsresearch.com"]},
            ),
        )
        db.commit()

        assert supplier is not None
        assert supplier.company == "Henan GP Chemicals Co., Ltd"
        assert supplier.contact_barrier == "third_party"
        assert db.query(Manager).count() == before
