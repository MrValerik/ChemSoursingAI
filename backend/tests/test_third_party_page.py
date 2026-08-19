"""Компанию, найденную на чужой странице, из списка не выбрасываем.

Запрос #37 «Menthyl lactate»: поиск нашёл пять компаний, а во вкладку
попали три. Закупщик сравнил числа и справедливо спросил, куда делись
остальные. Выпали не по стране — по роду страницы: перечень продавцов на
makepolo и страница-рейтинг производителей. Правило отсеивало их целиком,
потому что контакты на такой странице принадлежат владельцу сайта, а не
компании: справочник patenthub.cn отдавал caoxd@patenthub.cn, журнал об
масличных культурах — адрес редакции, и письмо ушло бы постороннему.

Отсев был слишком широк. Имя компании со страницы взять можно, а почту с
неё — нельзя. Теперь компания попадает в список без канала связи и с
пометкой, откуда она взялась, а контакт закупщик впишет сам, открыв её
сайт.

Единственное, чего в списке по-прежнему нет, — страницы, которая компанию
не называет вовсе: «Не определено (список производителей)» не имя, писать
по нему некому.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_third_party.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, Manager, Supplier, User
from app.services.search_trace import create_search_run
from app.services.supplier_registry import (
    names_a_company,
    register_qualified_candidate,
    found_on_someone_elses_page,
)


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_third_party.db"):
        os.remove("test_third_party.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_third_party.db"):
        os.remove("test_third_party.db")


def _register(db, run, **overrides):
    result = {
        "result_index": 0,
        "url": "https://example.test/page",
        "title": "Страница",
        "company_name": "Wuhan Yuancheng Technology Development Co., Ltd",
        "page_kind": "company_site",
        "supplier_type": "unknown",
        "confidence": 45,
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "contacts": {},
    }
    result.update(overrides)
    return register_qualified_candidate(db, search_run=run, result=result)


@pytest.fixture()
def run(client):
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas="61597-98-6", name="Menthyl lactate", owner_id=owner.id)
        db.add(rfq)
        db.flush()
        search_run = create_search_run(
            db,
            owner_id=owner.id,
            rfq_id=rfq.id,
            input_payload={"name": "Menthyl lactate", "country": "Китай"},
        )
        db.commit()
        yield db, search_run


# --- имя компании ---


def test_a_placeholder_is_not_a_company_name():
    for name in (
        "Не определено (список производителей)",
        "Не указана (платформа ECHEMI)",
        "Неизвестно",
        "Unknown",
        "",
    ):
        assert not names_a_company(name)


def test_a_real_name_is_a_company_name():
    for name in (
        "武汉远城科技发展有限公司",
        "Hebei Hongtao Biotechnology Co., Ltd",
        "Bisor",
    ):
        assert names_a_company(name)


# --- реестр ---


@pytest.mark.parametrize(
    "kind", ["marketplace_listing", "directory", "market_report", "scientific"]
)
def test_a_company_named_on_someone_elses_page_still_gets_into_the_list(run, kind):
    db, search_run = run

    supplier = _register(db, search_run, page_kind=kind)
    db.commit()

    assert supplier is not None
    assert supplier.company == "Wuhan Yuancheng Technology Development Co., Ltd"


def test_its_contacts_are_never_taken_from_that_page(run):
    """caoxd@patenthub.cn принадлежит справочнику, а не поставщику."""
    db, search_run = run
    before = db.query(Manager).count()

    supplier = _register(
        db,
        search_run,
        page_kind="directory",
        url="https://www.patenthub.cn/cpc/patent-107180",
        contacts={"emails": ["caoxd@patenthub.cn"]},
    )
    db.commit()

    assert supplier is not None
    assert db.query(Manager).count() == before
    assert supplier.contact_barrier == "third_party"


def test_the_buyer_is_told_where_the_company_came_from(run):
    """Без пометки строка выглядит как компания без связи и только."""
    db, search_run = run

    supplier = _register(db, search_run, page_kind="marketplace_listing")
    db.commit()

    assert supplier.contact_barrier == "third_party"


def test_a_page_that_names_no_company_stays_out(run):
    db, search_run = run

    supplier = _register(
        db,
        search_run,
        page_kind="market_report",
        company_name="Не определено (список производителей)",
        url="https://b2bdata.aipage.com/en/rank/all-area/",
    )
    db.commit()

    assert supplier is None


def test_the_company_own_page_still_gives_its_contacts(run):
    """Правило сужает только чужие страницы и не трогает обычный путь."""
    db, search_run = run

    supplier = _register(
        db,
        search_run,
        url="https://www.tnjchem.com/l-menthyl-lactate",
        company_name="TNJ Chemical",
        contacts={"emails": ["sales@tnjchem.com"]},
    )
    db.commit()

    assert supplier is not None
    assert supplier.contact_barrier is None
    emails = {m.email for m in db.query(Manager).filter(
        Manager.supplier_id == supplier.id
    )}
    assert "sales@tnjchem.com" in emails


def test_a_typed_contact_is_not_wiped_by_a_later_third_party_page(run):
    """Вписанный руками адрес переживает следующий прогон."""
    db, search_run = run

    supplier = _register(
        db,
        search_run,
        url="https://www.bisorgroup.com/zh/actibiso",
        company_name="Bisor",
    )
    db.flush()
    db.add(Manager(supplier_id=supplier.id, email="sales@bisorgroup.com"))
    db.commit()

    again = _register(
        db,
        search_run,
        url="https://directory.test/bisor",
        company_name="Bisor",
        page_kind="directory",
        contacts={"emails": ["editor@directory.test"]},
    )
    db.commit()

    assert again.id == supplier.id
    emails = {m.email for m in db.query(Manager).filter(
        Manager.supplier_id == supplier.id
    )}
    assert emails == {"sales@bisorgroup.com"}
    assert again.contact_barrier is None


# --- что видно, а что спрятано ---
#
# Правило одно на обе вкладки. Если находка попадает в «Отобранные
# компании», в «Найденных» она должна быть видна сразу, без разворачивания:
# иначе вкладки противоречат друг другу и закупщик идёт спрашивать, куда
# делась компания. Прогон 288 по #37 показал это наглядно — 武汉远城
# стояла в списке и пряталась в находках.


def test_a_company_site_is_its_own_page():
    assert not found_on_someone_elses_page({
        "company_name": "TNJ Chemical",
        "page_kind": "company_site",
    })


def test_a_storefront_is_its_own_page():
    """Магазин компании на площадке — всё-таки её страница."""
    assert not found_on_someone_elses_page({
        "company_name": "Hangzhou Keying Chem",
        "page_kind": "marketplace_storefront",
    })


@pytest.mark.parametrize(
    "kind", ["directory", "market_report", "scientific", "marketplace_listing"]
)
def test_a_directory_or_a_listing_is_someone_elses_page(kind):
    assert found_on_someone_elses_page({
        "company_name": "Hebei Hongtao Biotechnology Co., Ltd",
        "page_kind": kind,
    })


def test_whatever_reaches_the_list_is_visible_in_the_findings(run):
    """Спрятано ровно то, чего в списке компаний нет, — и ничего больше."""
    db, search_run = run
    for kind in ("company_site", "directory", "market_report"):
        result = {
            "result_index": 0,
            "url": f"https://example.test/{kind}",
            "company_name": "Hebei Hongtao Biotechnology Co., Ltd",
            "page_kind": kind,
            "supplier_type": "unknown",
            "confidence": 40,
            "gmp_status": "not_found",
            "iso_status": "not_found",
            "coa_status": "not_found",
            "tds_status": "not_found",
            "contacts": {},
        }
        hidden = not names_a_company(str(result["company_name"]))
        supplier = register_qualified_candidate(db, search_run=search_run, result=result)
        db.flush()

        assert supplier is not None, "компания в списке есть"
        assert not hidden, "значит, в находках она видна"
        # А отличается только происхождение: с чужой страницы контактов
        # не берём и ставим пометку.
        assert (supplier.contact_barrier == "third_party") is (
            found_on_someone_elses_page(result)
        )
    db.rollback()


def test_a_nameless_page_is_hidden_and_unregistered(run):
    """Единственный случай, когда находку прячем: компании на ней нет."""
    db, search_run = run
    result = {
        "result_index": 0,
        "url": "https://b2bdata.aipage.com/en/rank/all-area/",
        "company_name": "Не определено (список производителей)",
        "page_kind": "market_report",
        "supplier_type": "unknown",
        "confidence": 0,
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "contacts": {},
    }

    assert not names_a_company(str(result["company_name"]))
    assert register_qualified_candidate(db, search_run=search_run, result=result) is None
