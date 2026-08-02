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
    ("zauba.com", "Zauba", "reference"),
    ("zoominfo.com", "ZoomInfo", "reference"),
    ("linkedin.com", "LinkedIn", "reference"),
    ("facebook.com", "Facebook", "reference"),
    ("wikipedia.org", "Wikipedia", "reference"),
]


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
    """Делит выдачу на сайты компаний и посредников, сохраняя порядок."""
    direct: list[dict] = []
    intermediaries: list[dict] = []
    for result in results:
        target = intermediaries if is_intermediary(
            str(result.get("url") or ""), domains
        ) else direct
        target.append(result)
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
