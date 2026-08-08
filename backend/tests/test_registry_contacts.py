"""Контакт со страницы должен доехать до «Отобранных поставщиков».

Поиск и раньше заводил компанию в реестре, но без контакта. В таблице
отбора это тупик: галочку там поставить нельзя, пока у поставщика нет
канала, а канал берётся из контактов менеджера. То есть поиск доводил до
компании и останавливался, хотя почта и телефон лежали в уже загруженной
странице — связь нашлась у 93 карточек из 136.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_registry_contacts.db")

import pytest

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, User
from app.models.base import Base
from app.services.search_trace import create_search_run
from app.services.supplier_registry import register_qualified_candidate

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_registry_contacts.db"):
        os.remove("test_registry_contacts.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_registry_contacts.db"):
        os.remove("test_registry_contacts.db")


def _run(db, url: str):
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


def _result(url: str, company: str = "Пример Кемикал", **overrides) -> dict:
    # Имя у каждого случая своё: дедупликация идёт по имени компании,
    # и одинаковые имена схлопнулись бы в одну запись.
    result = {
        "result_index": 0,
        "url": url,
        "title": "Adipic acid supplier",
        "company_name": company,
        "supplier_type": "manufacturer",
        "confidence": 70,
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
    }
    result.update(overrides)
    return result


def test_contacts_from_the_page_become_a_channel(client):
    with SessionLocal() as db:
        run = _run(db, "https://example-a.cn/adipic")
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://example-a.cn/adipic",
                "Альфа Кемикал",
                contacts={
                    "emails": ["sales@example-a.cn"],
                    "whatsapp": ["+8615000000001"],
                    "phones": ["+86-21-000000"],
                },
            ),
        )
        db.commit()

        managers = db.query(Manager).filter(Manager.supplier_id == supplier.id).all()
        assert [m.email for m in managers] == ["sales@example-a.cn"]
        assert managers[0].whatsapp == "+8615000000001"
        # Вещество запроса — чтобы переписка знала, о чём писать.
        assert managers[0].offered_substances == ["Adipic acid"]


def test_a_second_run_does_not_duplicate_the_contact(client):
    with SessionLocal() as db:
        run = _run(db, "https://example-b.cn/adipic")
        payload = _result(
            "https://example-b.cn/adipic",
            "Бета Кемикал",
            contacts={"emails": ["info@example-b.cn"]},
        )
        supplier = register_qualified_candidate(db, search_run=run, result=payload)
        register_qualified_candidate(db, search_run=run, result=payload)
        db.commit()

        managers = db.query(Manager).filter(Manager.supplier_id == supplier.id).all()
        assert len(managers) == 1


def test_whatsapp_alone_is_still_a_way_to_write(client):
    with SessionLocal() as db:
        run = _run(db, "https://example-c.cn/adipic")
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://example-c.cn/adipic",
                "Гамма Кемикал",
                contacts={"whatsapp": ["+8615000000002"]},
            ),
        )
        db.commit()

        managers = db.query(Manager).filter(Manager.supplier_id == supplier.id).all()
        assert len(managers) == 1
        assert managers[0].email is None
        assert managers[0].whatsapp == "+8615000000002"


def test_a_marketplace_page_gives_no_contact(client):
    """Там отдел продаж площадки, а не компании — письмо ушло бы не туда."""
    with SessionLocal() as db:
        run = _run(db, "https://www.echemi.com/produce/x.html")
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://www.echemi.com/produce/x.html",
                "Дельта Кемикал",
                supplier_type="marketplace",
                contacts={"emails": ["service@echemi.com"]},
            ),
        )
        db.commit()

        assert db.query(Manager).filter(Manager.supplier_id == supplier.id).count() == 0


def test_the_platform_own_address_is_not_the_company_contact(client):
    """На витрине рядом с почтой компании стоит почта самой площадки.

    Наполнение реестра приписало service@chemball.com сразу трём разным
    китайским заводам — письмо ушло бы владельцу каталога. При этом
    собственный адрес компании на той же витрине брать надо: так нашлись
    info@jshonon.com и ethan@fuaochem.com.
    """
    with SessionLocal() as db:
        run = _run(db, "https://www.chemball.cn/factory/abc/product.html")
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://www.chemball.cn/factory/abc/product.html",
                "Ляньюньган Хэнмао",
                contacts={
                    "emails": ["service@chemball.com", "sales@hengmao.cn"],
                },
            ),
        )
        db.commit()

        emails = {
            m.email
            for m in db.query(Manager).filter(Manager.supplier_id == supplier.id)
        }
        assert emails == {"sales@hengmao.cn"}


def test_a_company_keeps_its_own_address_on_its_own_site(client):
    """Правило про площадку не должно трогать собственный сайт."""
    with SessionLocal() as db:
        run = _run(db, "https://www.gpcchem.com/adipic-acid.html")
        supplier = register_qualified_candidate(
            db,
            search_run=run,
            result=_result(
                "https://www.gpcchem.com/adipic-acid.html",
                "Дзета Кемикал",
                contacts={"emails": ["santo@gpcchem.com"]},
            ),
        )
        db.commit()

        emails = {
            m.email
            for m in db.query(Manager).filter(Manager.supplier_id == supplier.id)
        }
        assert emails == {"santo@gpcchem.com"}


def test_a_page_without_contacts_registers_the_company_anyway(client):
    """Компанию нашли — запись должна быть, просто без канала."""
    with SessionLocal() as db:
        run = _run(db, "https://example-d.cn/adipic")
        supplier = register_qualified_candidate(
            db, search_run=run, result=_result("https://example-d.cn/adipic", "Эпсилон Кемикал")
        )
        db.commit()

        assert supplier is not None
        assert db.query(Manager).filter(Manager.supplier_id == supplier.id).count() == 0
