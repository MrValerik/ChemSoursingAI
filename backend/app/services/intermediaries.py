"""Отсев посредников из выдачи до загрузки страниц.

Бюджет этапа ограничен числом загружаемых страниц, а не числом найденных
ссылок. Пока площадки отсеиваются после загрузки, они этот бюджет и съедают:
на стенде из 74 ссылок до оценки доходили пять, и все пять были торговыми
площадками. Отсев до загрузки тратит бюджет на доменные сайты компаний.
"""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intermediary import Intermediary

# Площадки и каталоги, встреченные в реальных прогонах. Список — стартовые
# данные, а не истина: он пополняется через интерфейс.
DEFAULT_INTERMEDIARIES: list[tuple[str, str, str]] = [
    ("echemi.com", "ECHEMI", "marketplace"),
    ("made-in-china.com", "Made-in-China", "marketplace"),
    ("alibaba.com", "Alibaba", "marketplace"),
    ("1688.com", "1688", "marketplace"),
    ("indiamart.com", "IndiaMART", "marketplace"),
    ("tradeindia.com", "TradeIndia", "marketplace"),
    ("exportersindia.com", "ExportersIndia", "marketplace"),
    ("ec21.com", "EC21", "marketplace"),
    ("ecplaza.net", "ECPlaza", "marketplace"),
    ("tradekey.com", "TradeKey", "marketplace"),
    ("globalsources.com", "Global Sources", "marketplace"),
    ("weiku.com", "Weiku", "marketplace"),
    ("chemicalbook.com", "ChemicalBook", "catalog"),
    ("chem960.com", "960 Chemical", "catalog"),
    ("chemball.cn", "Chemball", "catalog"),
    ("bio-equip.cn", "Bio-Equip", "marketplace"),
    ("b2bdata.baidu.com", "Baidu B2B Data", "reference"),
    ("guidechem.com", "Guidechem", "catalog"),
    ("lookchem.com", "LookChem", "catalog"),
    ("molbase.com", "Molbase", "catalog"),
    ("chemsrc.com", "Chemsrc", "catalog"),
    ("chembk.com", "ChemBK", "catalog"),
    ("chemblink.com", "ChemBlink", "catalog"),
    ("chemnet.com", "ChemNet", "catalog"),
    ("chemexper.com", "ChemExper", "catalog"),
    ("buyersguidechem.com", "Buyers Guide Chem", "catalog"),
    ("worldofchemicals.com", "WorldOfChemicals", "catalog"),
    ("pharmacompass.com", "PharmaCompass", "catalog"),
    ("chemicalregister.com", "ChemicalRegister", "catalog"),
    ("volza.com", "Volza", "reference"),
    ("cphi-online.com", "CPHI Online", "marketplace"),
    ("pharmaexcipients.com", "Pharma Excipients", "reference"),
    ("tracxn.com", "Tracxn", "reference"),
    ("barentz-na.com", "Barentz", "distributor"),
    ("specialchem.com", "SpecialChem", "catalog"),
    ("ulprospector.com", "UL Prospector", "catalog"),
    ("lookpolymers.com", "LookPolymers", "reference"),
    ("univarsolutions.com", "Univar Solutions", "distributor"),
    ("cmstudioplus.com", "CM Studio Plus", "catalog"),
    ("daltosur.com", "Daltosur", "distributor"),
    ("iajps.com", "IAJPS", "reference"),
    ("zauba.com", "Zauba", "reference"),
    ("zoominfo.com", "ZoomInfo", "reference"),
    ("linkedin.com", "LinkedIn", "reference"),
    ("facebook.com", "Facebook", "reference"),
    ("wikipedia.org", "Wikipedia", "reference"),
    # Встречены в прогонах 58-71 и попали в кандидаты как «не определён»:
    # реестр их не знал, и страница площадки шла наравне с сайтом завода.
    ("app17.com", "App17", "marketplace"),
    ("b2brazil.com", "B2Brazil", "marketplace"),
    ("kitairu.net", "Kitairu", "marketplace"),
    ("alu.cn", "Alu.cn", "marketplace"),
    ("gys.cn", "Gys.cn", "marketplace"),
    ("21food.cn", "21food", "marketplace"),
    ("aipage.com", "Aipage B2B Data", "reference"),
    ("chinacoat.net", "ChinaCoat", "reference"),
    ("yellowpages.com.vn", "Yellow Pages Vietnam", "reference"),
    ("blogspot.com", "Blogspot", "reference"),
]
# med-life.cn, gewhatman.cn и haoreagent.cn сюда не попали намеренно. Это
# магазины реактивов со своим сайтом, то есть контрагенты — пусть и
# перекупщики. Запись в реестр означает не только метку, но и отсев до
# загрузки, а отсеивать продавца за то, что он продавец, нельзя: в режиме
# поиска изготовителей его роль решается доказательствами.


def normalize_domain(value: str) -> str:
    """Домен без схемы, www и порта; регистр не учитывается."""
    candidate = (value or "").strip().lower()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or ""
    else:
        candidate = candidate.split("/")[0]
    candidate = candidate.split(":")[0]
    return candidate[4:] if candidate.startswith("www.") else candidate


def active_domains(db: Session) -> set[str]:
    """Действующие записи реестра одним запросом на этап поиска."""
    return {
        normalize_domain(domain)
        for domain in db.scalars(
            select(Intermediary.domain).where(Intermediary.is_active.is_(True))
        ).all()
    }


# Составные доменные зоны: без них у lookchem.com.cn меткой окажется «com».
# Список короткий намеренно — сюда попадают только зоны, встречающиеся у
# химических площадок и поставщиков.
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "com.cn", "net.cn", "org.cn", "gov.cn",
        "com.hk", "com.tw", "com.sg", "com.my",
        "co.in", "co.jp", "co.kr", "co.uk",
        "com.br", "com.au", "com.tr", "com.ua",
    }
)


def domain_label(value: str) -> str:
    """Имя площадки без доменной зоны и поддоменов.

    Берётся метка перед зоной, а не первая часть адреса: площадки выдают
    продавцам поддомены (``fortunegrowth.en.made-in-china.com``), и по первой
    точке меткой оказался бы продавец. Зеркала в разных зонах при этом дают
    одну метку: ``lookchem.com`` и ``lookchem.cn`` — это ``lookchem``.
    """
    host = normalize_domain(value)
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return host
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_LABEL_SUFFIXES:
        return parts[-3]
    return parts[-2]


# Поддомены, которые площадка выдаёт не продавцу, а себе: язык, зеркало,
# мобильная версия. Всё остальное перед именем площадки — имя продавца.
_GENERIC_SUBDOMAINS = frozenset(
    {
        "www", "m", "mobile", "amp", "en", "cn", "ru", "de", "es", "fr", "pt",
        "it", "ja", "ko", "ar", "chinese", "russian", "china", "us", "uk",
        "shop", "store", "app", "api", "static", "img", "www2",
        # Мобильные версии китайских площадок. Без них wap.guidechem.com
        # читался как магазин компании и уходил от отсева: в прогоне по
        # 4-хлорфенолу торговая страница площадки так и попала в кандидаты.
        "wap", "3g", "touch", "mip", "h5", "mobi", "web", "www3",
    }
)

# Пути, которыми площадка обозначает страницу одной компании.
_STOREFRONT_PATHS = (
    "/company-",
    "/company/",
    "/showroom/",
    "/supplier/",
    "/suppliers/",
    "/store/",
    "/stores/",
    "/shop-",
    "/seller/",
    "/manufacturer/",
    "/factory/",
)

# Только эти площадки действительно выделяют страницу одной компании.
# Универсальное правило по пути ``/company/`` ошибочно сохраняло LinkedIn и
# справочные сайты как будто это магазин производителя.
_STOREFRONT_LABELS = frozenset(
    {
        "made-in-china",
        "alibaba",
        "lookchem",
        "guidechem",
        "chemball",
        "echemi",
    }
)


def marketplace_page_kind(url: str) -> str:
    """Что это за страница на домене площадки: витрина или магазин компании.

    Разница существенная, и различать её приходится обязательно. Витрина
    (`made-in-china.com/products-search/…`, `lookchem.com/cas-107/…`)
    перечисляет многих продавцов и роль производителя подтвердить не может.
    Магазин одной компании (`xjleso.en.made-in-china.com`) называет
    конкретное предприятие и по содержанию не отличается от его сайта.

    Это не мелочь: крупнейший в мире производитель эпоксидированного
    соевого масла собственного сайта не имеет вовсе и существует только
    магазином на площадке. Отбрасывая площадку целиком, поиск делает его
    принципиально ненаходимым.
    """
    host = normalize_domain(url)
    parts = [part for part in host.split(".") if part]
    label = domain_label(host)
    if label not in _STOREFRONT_LABELS:
        return "listing"
    if label in parts:
        prefix = parts[: parts.index(label)]
        if any(part not in _GENERIC_SUBDOMAINS for part in prefix):
            return "storefront"
    path = urlparse(url if "//" in url else f"//{url}").path.casefold()
    if any(marker in path for marker in _STOREFRONT_PATHS):
        return "storefront"
    return "listing"


def is_intermediary(url: str, domains: set[str]) -> bool:
    """Принадлежит ли ссылка посреднику.

    Сравнение идёт по имени площадки без доменной зоны, поэтому зеркала вида
    ``lookchem.cn`` попадают под то же правило, что и ``lookchem.com``, а
    поддомены продавцов внутри площадки — под правило самой площадки.
    """
    host = normalize_domain(url)
    if not host:
        return False
    label = domain_label(host)
    return any(label == domain_label(domain) for domain in domains if domain)


def split_by_intermediary(
    results: list[dict], domains: set[str]
) -> tuple[list[dict], list[dict]]:
    """Делит выдачу на сайты компаний и посредников, сохраняя порядок.

    Магазин одной компании на домене площадки остаётся среди прямых
    источников: он называет предприятие, а не перечисляет продавцов.
    """
    direct: list[dict] = []
    intermediaries: list[dict] = []
    for result in results:
        url = str(result.get("url") or "")
        if is_intermediary(url, domains) and marketplace_page_kind(url) != "storefront":
            intermediaries.append(result)
        else:
            direct.append(result)
    return direct, intermediaries


def seed_intermediaries(db: Session) -> int:
    """Заполняет реестр стартовым списком, не трогая правки пользователя."""
    existing = {
        normalize_domain(domain)
        for domain in db.scalars(select(Intermediary.domain)).all()
    }
    added = 0
    for domain, name, kind in DEFAULT_INTERMEDIARIES:
        if normalize_domain(domain) in existing:
            continue
        db.add(Intermediary(domain=domain, name=name, kind=kind))
        added += 1
    if added:
        db.commit()
    return added
