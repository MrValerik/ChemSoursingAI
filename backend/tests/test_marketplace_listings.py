"""Продавцы из выдачи о площадке — единственный доступный путь к Echemi.

Сами страницы закрыты: все 29 когда-либо загруженных вернули HTTP 200 с
challenge защитного экрана вместо содержимого, одинаковые 34218 символов.
Проверено и со стенда, и снаружи. Обходить защиту мы не будем.

Зато описания в выдаче Google устроены строго единообразно, и из одной
строки достаются имя, страна и роль. Замер по сохранённым выдачам: из 50
ссылок на echemi разбирается 12 и даёт 10 различных компаний, ценой в
ноль запросов и ноль загрузок.
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_marketplace_listings.db")

from app.services.marketplace_listings import collect_sellers, parse_seller

ECHEMI = "https://www.echemi.com/produce/pr2112152898-acetylsalicylic-acid.html"


def _snippet(country: str, role: str, company: str) -> str:
    return (
        f"Contact {country} {role} {company} for the product "
        "Acetylsalicylic Acid CAS 50-78-2 . Chat now for more business."
    )


# --- разбор одной строки ---


def test_a_manufacturer_is_read_with_country_and_role():
    seller = parse_seller(
        ECHEMI, "Acetylsalicylic Acid CAS 50-78-2 - ECHEMI",
        _snippet("China", "Manufactory", "Shandong zhishang chemical Co.,Ltd"),
    )
    assert seller is not None
    assert seller.company == "Shandong zhishang chemical Co.,Ltd"
    assert seller.claimed_role == "manufacturer"
    assert seller.country == "Китай"
    assert seller.platform == "echemi"
    assert seller.truncated is False


def test_a_trader_and_a_distributor_are_read():
    trader = parse_seller(
        ECHEMI, "", _snippet("China", "Trader", "Shandong Ranhang Biotechnology Co., Ltd.")
    )
    distributor = parse_seller(
        ECHEMI, "", _snippet("United Kingdom", "Distributor", "Belle Chemical LLC")
    )
    assert trader.claimed_role == "trader"
    assert distributor.claimed_role == "distributor"
    assert distributor.country == "Великобритания"


def test_the_word_supplier_carries_no_role():
    """На площадке так подписан любой продавец — роли в этом нет."""
    seller = parse_seller(ECHEMI, "", _snippet("China", "Supplier", "Some Chemical Co."))
    assert seller is not None
    assert seller.claimed_role is None


def test_a_cut_off_name_is_marked():
    """Google обрезает длинные описания на полуслове."""
    seller = parse_seller(
        ECHEMI, "", _snippet("China", "Trader", "Hainan Flying International Trade Co., L")
    )
    assert seller.truncated is True


def test_a_reference_page_yields_nothing():
    """Справочник и паспорта безопасности компанию не называют."""
    assert (
        parse_seller(
            "https://www.echemi.com/sds/glycine-betaine-pd20150901034.html",
            "Betaine SDS, 107-43-7 Safety Data Sheets - ECHEMI",
            "Look through Betaine MSDS details show. We provide Betaine 107-43-7 "
            "safety data sheet view and download for free at Echemi.com .",
        )
        is None
    )


# --- второй шаблон: имя продавца в заголовке товарной карточки ---

# Живые заголовки из выдачи Google 21 августа 2026 по запросу
# `site:echemi.com Acetylsalicylic Acid 50-78-2 manufacturer`.
_PRODUCE = "https://www.echemi.com/produce/pr2203314189-factory-price-high-quality-aspirin.html"


def test_seller_name_is_read_from_the_product_title():
    seller = parse_seller(
        _PRODUCE,
        "Buy Factory Price High Quality Aspirin Acetylsalicylic Acid Powder "
        "CAS 50-78-2 from SHANDONG LOOK CHEMICAL CO.,LTD - ECHEMI",
        "",
    )
    assert seller is not None
    assert seller.company == "SHANDONG LOOK CHEMICAL CO.,LTD"
    # Заголовок не называет ни страны, ни роли — выдумывать их нечем.
    assert seller.claimed_role is None
    assert seller.country is None
    assert seller.truncated is False


def test_a_title_with_parentheses_and_plus_is_read():
    for title, expected in (
        (
            "Buy Aspirin 99.5% with REACH,GMP,FDA Pharmacy grade from "
            "JQC (Huayin) Pharmaceutical Co., Ltd. - ECHEMI",
            # Хвостовая точка срезается, как и в основном пути разбора.
            "JQC (Huayin) Pharmaceutical Co., Ltd",
        ),
        (
            "Buy Aspirin/Acetylsalicylic acid CAS: 50-78-2 Pharma Grade "
            "from SHANHAI YUKING+POVIDONE - ECHEMI",
            "SHANHAI YUKING+POVIDONE",
        ),
    ):
        seller = parse_seller(_PRODUCE, title, "")
        assert seller is not None, title
        assert seller.company == expected


def test_a_cut_off_title_name_is_marked():
    seller = parse_seller(
        _PRODUCE,
        "Buy Factory Supply CAS 50-78-2 Acetylsalicylic Acid/Aspirin Power "
        "from Hainan Flying International Trade Co., L - ECHEMI",
        "",
    )
    assert seller is not None
    assert seller.truncated is True


def test_reference_titles_on_the_same_domain_are_not_sellers():
    """Справочные разделы называются, но продавца в заголовке не имеют."""
    for url, title in (
        (
            "https://www.echemi.com/productsInformation/pd20150901125-aspirin.html",
            "Aspirin Price and Market Analysis - ECHEMI - ECHEMI.com",
        ),
        (
            "https://www.echemi.com/products/pd20150901125-aspirin.html",
            "Aspirin | 50-78-2, Aspirin Formula - ECHEMI",
        ),
        (
            "https://www.echemi.com/sds/aspirin-pd20150901125.html",
            "Aspirin SDS, 50-78-2 Safety Data Sheets - ECHEMI",
        ),
    ):
        assert parse_seller(url, title, "") is None, title


def test_the_title_template_only_fires_on_product_pages():
    """Тот же заголовок на справочном пути продавца не создаёт."""
    title = "Buy Aspirin from SHANDONG LOOK CHEMICAL CO.,LTD - ECHEMI"
    assert parse_seller(_PRODUCE, title, "") is not None
    assert (
        parse_seller("https://www.echemi.com/products/pd20150901125.html", title, "")
        is None
    )


def test_snippet_wins_over_title_when_both_are_present():
    """Описание богаче заголовка: в нём есть страна и роль."""
    seller = parse_seller(
        _PRODUCE,
        "Buy Aspirin from Some Other Name Co.,Ltd - ECHEMI",
        _snippet("China", "Manufactory", "Shandong Look Chemical Co.,Ltd"),
    )
    assert seller.company == "Shandong Look Chemical Co.,Ltd"
    assert seller.claimed_role == "manufacturer"
    assert seller.country == "Китай"


def test_another_site_is_not_a_marketplace_listing():
    assert parse_seller(
        "https://www.gpcchem.com/adipic-acid.html",
        "Adipic acid",
        _snippet("China", "Manufactory", "Henan GP Chemicals Co.,Ltd"),
    ) is None


# --- пачка результатов ---


def test_one_seller_from_several_cards_is_one_record():
    results = [
        {"url": ECHEMI, "title": "", "snippet": _snippet("China", "Trader", "Xian ZB Biotech Co.,Ltd")},
        {"url": ECHEMI + "?x=2", "title": "", "snippet": _snippet("China", "Trader", "Xian ZB Biotech Co.,Ltd")},
    ]
    assert len(collect_sellers(results)) == 1


def test_a_whole_name_wins_over_a_cut_one():
    cut = _snippet("China", "Trader", "Hainan Flying International Trade Co., L")
    whole = _snippet("China", "Trader", "Hainan Flying International Trade Co., Ltd.")
    sellers = collect_sellers(
        [
            {"url": ECHEMI, "title": "", "snippet": cut},
            {"url": ECHEMI + "?x=2", "title": "", "snippet": whole},
        ]
    )
    # Обрезанное и целое имя — разные ключи; важно, что целое сохранилось
    # и помечено неусечённым.
    assert any(not s.truncated for s in sellers)


def test_pages_without_sellers_are_skipped():
    assert collect_sellers([{"url": ECHEMI, "title": "", "snippet": "Betaine price"}]) == []
    assert collect_sellers([]) == []


# --- запись в реестр ---


@pytest.fixture(scope="module")
def client():
    """Своя база на модуль: иначе записи переживают прогон и мешают счёту."""
    from fastapi.testclient import TestClient

    from app.core.db import engine
    from app.main import app

    if os.path.exists("test_marketplace_listings.db"):
        os.remove("test_marketplace_listings.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_marketplace_listings.db"):
        os.remove("test_marketplace_listings.db")


def test_a_platform_seller_is_registered_without_pretending_to_be_verified(client):
    """У него нет ни страницы, ни доказательств — только строка выдачи."""
    from app.core.db import SessionLocal
    from app.models import RFQ, User
    from app.services.search_trace import create_search_run
    from app.services.supplier_registry import register_marketplace_seller

    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas="50-78-2", name="Аспирин", owner_id=owner.id)
        db.add(rfq)
        db.flush()
        run = create_search_run(
            db,
            owner_id=owner.id,
            rfq_id=rfq.id,
            input_payload={"cas": "50-78-2", "name": "Аспирин"},
        )
        seller = parse_seller(
            ECHEMI, "", _snippet("China", "Manufactory", "Shandong Look Chemical Co.,Ltd")
        )

        supplier = register_marketplace_seller(db, search_run=run, seller=seller)
        db.commit()

        assert supplier is not None
        assert supplier.company == "Shandong Look Chemical Co.,Ltd"
        assert supplier.country == "Китай"
        # Балл не выставляется: проверять было нечего.
        assert supplier.evidence_score is None
        # Связи нет, и причина названа.
        assert supplier.contact_barrier == "platform"
        assert "площадки" in (supplier.reputation or "")


def test_a_second_run_does_not_duplicate_the_platform_seller(client):
    from app.core.db import SessionLocal
    from app.models import RFQ, Supplier, User
    from app.services.search_trace import create_search_run
    from app.services.supplier_registry import register_marketplace_seller

    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas="50-78-2", name="Аспирин", owner_id=owner.id)
        db.add(rfq)
        db.flush()
        run = create_search_run(
            db,
            owner_id=owner.id,
            rfq_id=rfq.id,
            input_payload={"cas": "50-78-2", "name": "Аспирин"},
        )
        seller = parse_seller(
            ECHEMI, "", _snippet("China", "Trader", "Xian ZB Biotech Co.,Ltd")
        )
        before = db.query(Supplier).count()
        first = register_marketplace_seller(db, search_run=run, seller=seller)
        db.commit()
        after_first = db.query(Supplier).count()

        second = register_marketplace_seller(db, search_run=run, seller=seller)
        db.commit()
        after_second = db.query(Supplier).count()

        assert first.id == second.id
        # Первая встреча заводит запись, вторая — нет.
        assert after_first == before + 1
        assert after_second == after_first
