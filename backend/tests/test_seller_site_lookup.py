"""Собственный сайт компании, названной площадкой.

С Echemi мы получаем только имя: страницы закрыты защитным экраном, а
описание в выдаче Google связи не несёт. Компания попадает в реестр без
единого способа написать ей.

Проверка принадлежности сайта здесь — не формальность. Замер 07.09.2026 на
шести компаниях из сохранённых выдач: сайт-кандидат нашёлся у всех шести,
но `senwayer.com` принадлежал другой компании, а `importgenius.cn` —
агрегатору судовых записей, у которого имя компании стоит в заголовке
страницы. Оба проходят проверку по заголовку и оба отсеиваются по домену.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_seller_site_lookup.db")

from app.connectors.web_page import FetchedPage, PageFetchError
from app.services.marketplace_listings import site_belongs_to_company
from app.services.seller_site_lookup import find_seller_site


def _page(url: str, text: str, *, contact_links: tuple[str, ...] = ()) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        domain=url.split("/")[2],
        title="About us",
        content_type="text/html",
        http_status=200,
        text=text,
        content_hash="hash",
        contact_links=contact_links,
    )


def _search(*urls: str):
    def run(query: str, limit: int = 8) -> list[dict]:
        return [{"url": url, "title": "", "snippet": ""} for url in urls[:limit]]

    return run


# --- принадлежность сайта ---


def test_the_domain_carrying_the_company_name_belongs_to_it():
    assert site_belongs_to_company(
        "Shandong zhishang chemical Co.,Ltd", "https://zhishangchem.com/"
    )
    assert site_belongs_to_company("Belle Chemical LLC", "https://bellechemical.com/")
    assert site_belongs_to_company("SHANHAI YUKING+POVIDONE", "https://en.yukingchina.com/")


def test_an_aggregator_naming_the_company_does_not_belong_to_it():
    # importgenius.cn — база судовых записей. Имя компании стоит в
    # заголовке её страницы, и сверка по заголовку пропустила бы её как
    # сайт поставщика, а письмо ушло бы владельцу агрегатора.
    assert not site_belongs_to_company(
        "Shandong Ranhang Biotechnology Co., Ltd",
        "https://importgenius.cn/china-suppliers/shandong-ranhang",
    )


def test_a_different_companys_site_does_not_belong_to_it():
    assert not site_belongs_to_company(
        "Dingwang Technology (Wuhan) Co., Ltd", "https://senwayer.com/"
    )


def test_a_generic_word_alone_does_not_prove_ownership():
    # Иначе «chemical» в домене признавал бы своим сайтом почти любую
    # химическую компанию.
    assert not site_belongs_to_company(
        "Hunan Huateng Pharmaceutical Co., Ltd", "https://chemicalbook.com/"
    )


# --- поиск сайта и контактов ---


def test_contacts_are_taken_from_the_companys_own_site():
    pages = {
        "https://zhishangchem.com/": _page(
            "https://zhishangchem.com/", "Email: sales@zhishangchem.com"
        )
    }
    site = find_seller_site(
        "Shandong zhishang chemical Co.,Ltd",
        search=_search("https://zhishangchem.com/"),
        fetch=lambda url: pages[url],
    )
    assert site is not None
    assert site.url == "https://zhishangchem.com/"
    assert site.contacts["emails"] == ["sales@zhishangchem.com"]


def test_a_site_that_is_not_the_companys_is_never_opened():
    def fetch(url: str):
        raise AssertionError(f"чужая страница не должна загружаться: {url}")

    assert (
        find_seller_site(
            "Dingwang Technology (Wuhan) Co., Ltd",
            search=_search("https://senwayer.com/", "https://importgenius.cn/x"),
            fetch=fetch,
        )
        is None
    )


def test_a_known_platform_is_skipped_even_when_the_name_matches():
    # Мы пришли с площадки и на неё же вернулись бы: имя компании стоит и
    # в её витрине, и в домене зеркала.
    def fetch(url: str):
        raise AssertionError(f"площадка не должна загружаться: {url}")

    assert (
        find_seller_site(
            "Echemi Chemical Co., Ltd",
            platforms={"echemi.com"},
            search=_search("https://www.echemi.com/company/echemi.html"),
            fetch=fetch,
        )
        is None
    )


def test_the_contacts_page_is_loaded_when_the_front_page_has_none():
    pages = {
        "https://bellechemical.com/": _page(
            "https://bellechemical.com/",
            "Belle Chemical produces fine chemicals.",
            contact_links=("https://bellechemical.com/contact",),
        ),
        "https://bellechemical.com/contact": _page(
            "https://bellechemical.com/contact", "Mail: info@bellechemical.com"
        ),
    }
    site = find_seller_site(
        "Belle Chemical LLC",
        search=_search("https://bellechemical.com/"),
        fetch=lambda url: pages[url],
    )
    assert site is not None
    assert site.contacts["emails"] == ["info@bellechemical.com"]
    assert site.fetched_urls == (
        "https://bellechemical.com/",
        "https://bellechemical.com/contact",
    )


def test_a_site_without_contacts_is_still_a_result():
    # Адрес сайта сам по себе полезен: до сих пор у компании с площадки не
    # было и его, а причину закупщик читает в барьере.
    pages = {
        "https://bellechemical.com/": _page(
            "https://bellechemical.com/", "Email: [email protected]"
        )
    }
    site = find_seller_site(
        "Belle Chemical LLC",
        search=_search("https://bellechemical.com/"),
        fetch=lambda url: pages[url],
    )
    assert site is not None
    assert site.contacts == {}
    assert site.barrier == "obfuscated"


def test_an_unreachable_site_does_not_stop_the_run():
    def fetch(url: str):
        raise PageFetchError("403")

    assert (
        find_seller_site(
            "Belle Chemical LLC",
            search=_search("https://bellechemical.com/"),
            fetch=fetch,
        )
        is None
    )


def test_a_broken_search_does_not_stop_the_run():
    def search(query: str, limit: int = 8):
        raise RuntimeError("провайдер поиска недоступен")

    assert (
        find_seller_site("Belle Chemical LLC", search=search, fetch=lambda url: None)
        is None
    )


# --- бюджет этапа ---


class _NoQueries:
    def refuse_query(self):
        return "query_budget"

    def refuse_page_fetch(self):
        return None


class _NoFetches:
    def refuse_query(self):
        return None

    def refuse_page_fetch(self):
        return "page_budget"


def test_the_query_is_not_spent_beyond_the_budget():
    def search(query: str, limit: int = 8):
        raise AssertionError("запрос сверх бюджета не должен уходить")

    assert (
        find_seller_site("Belle Chemical LLC", budget=_NoQueries(), search=search)
        is None
    )


def test_the_page_is_not_fetched_beyond_the_budget():
    def fetch(url: str):
        raise AssertionError("загрузка сверх бюджета не должна выполняться")

    assert (
        find_seller_site(
            "Belle Chemical LLC",
            budget=_NoFetches(),
            search=_search("https://bellechemical.com/"),
            fetch=fetch,
        )
        is None
    )
