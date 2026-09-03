"""Роль компании читается со страницы «о компании», а не с карточки товара.

Замер по 45 сохранённым страницам прогонов 328–336 (семь позиций заказчика):
утверждения о роли нет на 37 из них — и не в отданной модели части, а во всём
тексте. Слово manufacturer стоит на 37 страницах, но это вывеска
«manufacturer & supplier», и доказательством она справедливо не считается.

Обрезка страницы тут почти ни при чём: маркер роли за линией обреза лежит на
пяти страницах, из которых две — справочники, а одна уже доказана. Зато на
странице о себе компания роль называет: по 19 открывшимся разделам штатные
читатели фактов дали производственную площадку у 4 и офисный адрес у 3.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_company_profile.db")

from app.api.supplier_search import (  # noqa: E402
    QualificationEvidence,
    SupplierQualification,
    _inject_deterministic_evidence,
    _page_proves_role,
)
from app.connectors.web_page import find_profile_links  # noqa: E402
from app.models.search_trace import SourceDocument  # noqa: E402


def _qualification(**kw) -> SupplierQualification:
    base = dict(
        result_index=0,
        company_name="Handom Chemicals",
        title_ru="Поставщик тетраметилдецинедиола",
        summary_ru="Китайская компания, товарная страница о роли молчит.",
        supplier_type="manufacturer",
        cas_status="not_found",
        country_status="claimed",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="not_found",
        tds_status="not_found",
        confidence=45,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )
    base.update(kw)
    return SupplierQualification(**base)


def _source(text: str, doc_id: int, url: str) -> SourceDocument:
    source = SourceDocument(
        search_run_id=1,
        agent_run_id=1,
        url=url,
        domain="www.handomchemicals.com",
        status="completed",
        text_content=text,
    )
    source.id = doc_id
    return source


# Товарная страница настоящего кандидата: вывеска есть, доказательства нет.
PRODUCT_PAGE = (
    "Handom Chemicals - China Manufacturer & Supplier\n"
    "Tetramethyldecynediol 99% purity, CAS 126-86-3\n"
    "Packing: 25kg drum. Price: negotiable. Send inquiry.\n"
)
PROFILE_PAGE = (
    "About Us\n"
    "With 10+ years of export experience, our factory covers an area of "
    "20,000 square meters in the Jiangsu chemical industrial park.\n"
    "We serve customers in 40 countries.\n"
)


def test_ссылка_на_раздел_о_компании_берётся_из_разметки():
    html = (
        '<a href="/news/2024-price">Company news</a>'
        '<a href="/about-us.html">About Us</a>'
        '<a href="/product/list">Products</a>'
    )
    links = find_profile_links(html, "https://www.handomchemicals.com/item/1")
    assert links == ("https://www.handomchemicals.com/about-us.html",)


def test_новости_и_доставка_разделом_о_компании_не_считаются():
    html = (
        '<a href="/about-shipping">About shipping</a>'
        '<a href="/news/about-us-award">About us award</a>'
    )
    assert find_profile_links(html, "https://example.cn/") == ()


def test_китайский_раздел_о_компании_находится_по_подписи():
    html = '<a href="/gsjj/">公司简介</a>'
    links = find_profile_links(html, "https://www.betelychina.com/p/9")
    assert links == ("https://www.betelychina.com/gsjj/",)


def test_товарная_страница_роль_не_доказывает():
    assert _page_proves_role(PRODUCT_PAGE) is False


def test_страница_о_компании_роль_доказывает():
    assert _page_proves_role(PROFILE_PAGE) is True


def test_площадка_со_страницы_о_компании_становится_доказательством():
    qualification = _qualification()
    product = _source(PRODUCT_PAGE, 1, "https://www.handomchemicals.com/item/1")
    profile = _source(PROFILE_PAGE, 2, "https://www.handomchemicals.com/about-us.html")

    _inject_deterministic_evidence(
        {0: qualification},
        cas="126-86-3",
        country="Китай",
        source_documents={1: product, 2: profile},
        source_indexes={1: 0, 2: 0},
        profile_documents={0: profile},
    )

    sites = [
        item
        for item in qualification.evidence
        if item.claim_type == "production_site"
    ]
    assert len(sites) == 1
    # Источником записан именно раздел «о компании»: цитата проверяется по
    # его тексту, и в товарной странице её нет.
    assert sites[0].source_document_id == profile.id
    assert "covers an area of" in sites[0].quote
    assert sites[0].quote in PROFILE_PAGE


def test_о_веществе_страница_о_компании_не_свидетельствует():
    """Номер вещества на странице о себе — не доказательство идентичности.

    Она рассказывает, кто эта компания, а не что продаётся на карточке. Иначе
    любой перечень продукции в подвале подтверждал бы совпадение вещества.
    """
    qualification = _qualification()
    product = _source(PRODUCT_PAGE, 1, "https://www.handomchemicals.com/item/1")
    profile_with_cas = _source(
        PROFILE_PAGE + "Our range covers CAS 9004-99-3 and others.\n",
        2,
        "https://www.handomchemicals.com/about-us.html",
    )

    _inject_deterministic_evidence(
        {0: qualification},
        cas="9004-99-3",
        country="Китай",
        source_documents={1: product, 2: profile_with_cas},
        source_indexes={1: 0, 2: 0},
        profile_documents={0: profile_with_cas},
    )

    identity = [
        item
        for item in qualification.evidence
        if item.claim_type == "chemical_identity"
    ]
    assert identity == []


def test_уже_доказанную_площадку_страница_о_компании_не_дублирует():
    qualification = _qualification(
        evidence=[
            QualificationEvidence(
                source_document_id=1,
                claim_type="production_site",
                claim_value="Собственная площадка указана на странице",
                support_status="supports",
                quote="our own factory in Jiangsu",
            )
        ]
    )
    product = _source(
        PRODUCT_PAGE + "our own factory in Jiangsu\n",
        1,
        "https://www.handomchemicals.com/item/1",
    )
    profile = _source(PROFILE_PAGE, 2, "https://www.handomchemicals.com/about-us.html")

    _inject_deterministic_evidence(
        {0: qualification},
        cas="126-86-3",
        country="Китай",
        source_documents={1: product, 2: profile},
        source_indexes={1: 0, 2: 0},
        profile_documents={0: profile},
    )

    sites = [
        item
        for item in qualification.evidence
        if item.claim_type == "production_site"
    ]
    assert len(sites) == 1
    assert sites[0].source_document_id == 1


def test_без_страницы_о_компании_поведение_прежнее():
    qualification = _qualification()
    product = _source(PRODUCT_PAGE, 1, "https://www.handomchemicals.com/item/1")

    _inject_deterministic_evidence(
        {0: qualification},
        cas="126-86-3",
        country="Китай",
        source_documents={1: product},
        source_indexes={1: 0},
    )

    assert [
        item
        for item in qualification.evidence
        if item.claim_type == "production_site"
    ] == []
