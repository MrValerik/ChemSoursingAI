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


def is_intermediary(url: str, domains: set[str]) -> bool:
    """Принадлежит ли ссылка посреднику.

    Сравнение идёт по суффиксу домена: у площадок поддомен на компанию —
    обычное дело (``shop.echemi.com``, ``supplier.made-in-china.com``), и
    правило должно распространяться на них тоже.
    """
    host = normalize_domain(url)
    if not host:
        return False
    return any(
        host == domain or host.endswith(f".{domain}") for domain in domains
    )


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
