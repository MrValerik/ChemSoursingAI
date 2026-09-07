"""Собственный сайт компании, названной площадкой (ADR-0001, вариант B).

С Echemi мы вычитываем только имя компании: страницы площадки отдают
challenge защитного экрана, а описание в выдаче Google несёт имя, страну и
роль — и ни одного способа связи. Компания в реестре появляется, писать ей
некуда.

Здесь имя превращается в канал связи: один поисковый запрос про компанию,
проверка, что найденный домен принадлежит ей, и контакты с её собственного
сайта.

Почему проверка принадлежности вынесена в отдельное правило и почему она
идёт по домену, а не по заголовку. Замер 07.09.2026 на шести компаниях из
сохранённых выдач: сайт-кандидат нашёлся у всех шести, но `senwayer.com`
оказался чужой компанией, а `importgenius.cn` — агрегатором судовых
записей, у которого имя компании стоит прямо в заголовке страницы. По
заголовку оба проходят проверку, по домену — оба отсеиваются. Без этого
правила агрегаторы попали бы в реестр как сайты поставщиков, а письма — их
владельцам.

Стоит это 0,24 запроса на прогон (+2,4% к нынешним десяти): компаний с
площадок мало, они повторяются между прогонами, и найденный однажды сайт
второй раз не ищется.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.connectors.web_page import FetchedPage, PageFetchError, fetch_web_page
from app.connectors.web_search import search_web
from app.services.contacts import find_contact_barrier, find_contacts, has_contacts
from app.services.intermediaries import is_intermediary
from app.services.marketplace_listings import site_belongs_to_company

# Запрос про компанию, а не про вещество: вещество приведёт обратно на
# площадки, где мы её и нашли.
SITE_QUERY = "{company} official website contact email"

# Сколько результатов смотреть. Собственный сайт компании стоит в первых
# строках или не стоит вовсе: дальше идут каталоги и агрегаторы, которые
# проверку принадлежности всё равно не пройдут.
_RESULT_LIMIT = 6

# Барьер для реестра: сайт компании найти не удалось. Отличается от
# «platform» тем, что попытка была и не удалась, — повторять её каждый
# прогон незачем.
BARRIER_SITE_NOT_FOUND = "site_not_found"


@dataclass(frozen=True)
class SellerSite:
    """Найденный собственный сайт компании и снятые с него контакты."""

    company: str
    url: str
    title: str | None
    contacts: dict[str, list[str]]
    # Почему контактов нет, если их нет: адрес подменён или стоит форма.
    barrier: str | None = None
    # Адреса, которые пришлось загрузить. Нужны для следа прогона: по ним
    # видно, за что списан бюджет загрузок.
    fetched_urls: tuple[str, ...] = ()


def _load(
    url: str,
    *,
    budget,
    fetch,
) -> FetchedPage | None:
    if budget is not None and budget.refuse_page_fetch() is not None:
        return None
    try:
        return fetch(url)
    except PageFetchError:
        return None
    except Exception:  # сеть, разбор, кодировки — не повод ронять прогон
        return None


def find_seller_site(
    company: str,
    *,
    budget=None,
    platforms: set[str] | None = None,
    search=None,
    fetch=None,
) -> SellerSite | None:
    """Ищет собственный сайт компании и снимает с него контакты.

    Возвращает None, если ни один результат выдачи не принадлежит компании:
    имя в заголовке чужой страницы сайтом компании её не делает.

    Сайт может найтись без контактов — тогда `contacts` пуст, а `barrier`
    объясняет причину. Это всё равно результат: закупщику есть куда пойти
    руками, а до сих пор у такой компании не было и адреса сайта.
    """
    # Значения берутся здесь, а не в объявлении: подстановка в объявлении
    # привязывает функцию поиска навсегда, и подменить её на прогоне уже
    # нельзя ни в тесте, ни в замере.
    search = search or search_web
    fetch = fetch or fetch_web_page
    company = (company or "").strip()
    if not company:
        return None
    if budget is not None and budget.refuse_query() is not None:
        return None
    try:
        results = search(SITE_QUERY.format(company=company), limit=_RESULT_LIMIT)
    except Exception:
        return None

    known = platforms or set()
    for item in results or []:
        url = str((item or {}).get("url") or "")
        if not url:
            continue
        # Площадка сама себя сайтом компании назвать не может, даже если
        # её домен случайно созвучен имени.
        if is_intermediary(url, set(known)):
            continue
        if not site_belongs_to_company(company, url):
            continue

        page = _load(url, budget=budget, fetch=fetch)
        if page is None:
            continue
        fetched = [page.final_url or url]
        contacts = find_contacts(page.text)
        barrier = None
        if not has_contacts(contacts):
            barrier = find_contact_barrier(page.text)
            # Связь чаще лежит на отдельной странице контактов, чем на
            # главной. Одна догрузка — по ссылке из разметки, а не по
            # угаданному адресу «/contact.html».
            for link in page.contact_links[:1]:
                extra = _load(link, budget=budget, fetch=fetch)
                if extra is None:
                    continue
                fetched.append(extra.final_url or link)
                found = find_contacts(extra.text)
                if has_contacts(found):
                    contacts = found
                    barrier = None
                    break
                barrier = barrier or find_contact_barrier(extra.text)
        return SellerSite(
            company=company,
            url=page.final_url or url,
            title=page.title,
            contacts=contacts if has_contacts(contacts) else {},
            barrier=barrier,
            fetched_urls=tuple(fetched),
        )
    return None
