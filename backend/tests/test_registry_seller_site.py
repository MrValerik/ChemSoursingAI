"""Найденный сайт продавца доезжает до реестра, и ищется он один раз.

Продавец с площадки заводится без связи: писать ему можно только через
Echemi, а её страницы закрыты защитным экраном. Поиск сайта по названию
это чинит, но стоит поискового запроса — значит повторять его каждый
прогон нельзя. Признак «уже искали» здесь не отдельное поле, а результат
самой попытки: удачная кладёт в источник сайт компании, неудачная ставит
барьер.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_registry_seller_site.db")

import pytest

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, User
from app.services.marketplace_listings import MarketplaceSeller
from app.services.search_trace import create_search_run
from app.services.seller_site_lookup import SellerSite
from app.services.supplier_registry import (
    needs_site_lookup,
    record_seller_site,
    record_seller_site_not_found,
    register_marketplace_seller,
)

from fastapi.testclient import TestClient

DB_FILE = "test_registry_seller_site.db"


@pytest.fixture(scope="module")
def client():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)


def _seller(company: str) -> MarketplaceSeller:
    return MarketplaceSeller(
        company=company,
        platform="echemi",
        listing_url="https://www.echemi.com/produce/pr21121-aspirin.html",
        claimed_role="manufacturer",
        country="Китай",
    )


def _register(db, company: str):
    owner = db.query(User).filter(User.username == "ivanov").one()
    rfq = RFQ(cas="50-78-2", name="Aspirin", owner_id=owner.id)
    db.add(rfq)
    db.flush()
    run = create_search_run(
        db,
        owner_id=owner.id,
        rfq_id=rfq.id,
        input_payload={"cas": "50-78-2", "name": "Aspirin", "country": "Китай"},
    )
    return register_marketplace_seller(db, search_run=run, seller=_seller(company))


def test_a_seller_from_a_platform_is_asked_for_its_own_site(client):
    with SessionLocal() as db:
        supplier = _register(db, "Shandong zhishang chemical Co.,Ltd")
        db.commit()
        # Связи нет, источник — витрина площадки: спрашивать сайт есть смысл.
        assert needs_site_lookup(db, supplier)


def test_the_found_site_becomes_the_source_and_the_contact(client):
    with SessionLocal() as db:
        supplier = _register(db, "Belle Chemical LLC")
        db.commit()
        record_seller_site(
            db,
            supplier=supplier,
            site=SellerSite(
                company="Belle Chemical LLC",
                url="https://bellechemical.com/contact",
                title="Contact",
                contacts={"emails": ["info@bellechemical.com"]},
            ),
            substance="Aspirin",
        )
        db.commit()

        assert supplier.source == "https://bellechemical.com/contact"
        assert supplier.contact_barrier is None
        emails = db.query(Manager).filter(Manager.supplier_id == supplier.id).all()
        assert [manager.email for manager in emails] == ["info@bellechemical.com"]
        # Второй раз запрос уже не тратится.
        assert not needs_site_lookup(db, supplier)


def test_the_note_about_an_unavailable_page_is_replaced_not_appended(client):
    # Иначе в карточке стояло бы «страница компании недоступна, проверка не
    # проводилась; собственный сайт найден поиском по названию» — строка,
    # противоречащая сама себе. Сведения площадки при этом остаются: роль
    # по-прежнему названа продавцом о себе.
    with SessionLocal() as db:
        supplier = _register(db, "Wuhan Oner Biotech Co.,Ltd")
        db.commit()
        assert "страница компании недоступна" in supplier.reputation

        record_seller_site(
            db,
            supplier=supplier,
            site=SellerSite(
                company="Wuhan Oner Biotech Co.,Ltd",
                url="https://onerbio.com/",
                title="Oner Bio",
                contacts={"emails": ["admin@onerbio.com"]},
            ),
            substance="Aspirin",
        )
        db.commit()

        assert "страница компании недоступна" not in supplier.reputation
        assert supplier.reputation == (
            "Сведения площадки echemi: manufacturer; "
            "собственный сайт найден поиском по названию"
        )


def test_a_site_without_contacts_replaces_the_platform_barrier(client):
    with SessionLocal() as db:
        supplier = _register(db, "Qingdao Nova Chemical Co., Ltd")
        db.commit()
        assert supplier.contact_barrier == "platform"
        record_seller_site(
            db,
            supplier=supplier,
            site=SellerSite(
                company="Qingdao Nova Chemical Co., Ltd",
                url="https://novachem.cn/",
                title="Nova",
                contacts={},
                barrier="obfuscated",
            ),
            substance="Aspirin",
        )
        db.commit()
        # «Адрес подменён» закупщик прочитает как «откройте и посмотрите
        # глазами», а «связь через площадку» — как «другого пути нет».
        assert supplier.contact_barrier == "obfuscated"
        assert not needs_site_lookup(db, supplier)


def test_a_failed_lookup_is_not_repeated_next_run(client):
    with SessionLocal() as db:
        supplier = _register(db, "Hainan Flying International Trade Co., Ltd")
        db.commit()
        record_seller_site_not_found(supplier)
        db.commit()
        assert supplier.contact_barrier == "site_not_found"
        assert not needs_site_lookup(db, supplier)


def test_a_company_known_by_its_own_site_is_never_asked(client):
    with SessionLocal() as db:
        supplier = _register(db, "Yuking China Co., Ltd")
        supplier.source = "https://yukingchina.com/"
        db.commit()
        assert not needs_site_lookup(db, supplier)
