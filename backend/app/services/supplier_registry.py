"""Idempotent registration of AI-qualified supplier candidates."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Manager, RfqSupplierLink, SearchRun, Supplier
from app.models.enums import SupplierType
from app.services.intermediaries import active_domains, domain_label, is_intermediary
from app.services.search_trace import utc_now

# Сколько контактов одной компании имеет смысл заводить. Больше — это уже
# не отдел продаж, а разбор чужого списка рассылки.
_MAX_MANAGERS = 3

_SEPARATORS_RE = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ一-鿿]+")
# Юридические хвосты компанию не различают, а сравнению мешают.
_LEGAL_TAILS = (
    "coltd", "co", "ltd", "limited", "inc", "llc", "gmbh", "corporation",
    "corp", "group", "company", "plc", "sa", "bv", "pvt", "ag", "kg",
)
# Короткое имя после нормализации слишком легко совпадает случайно.
_MIN_KEY_LENGTH = 5

# Заглушки вместо имени. Две нераспознанные компании — это две разные
# компании, и схлопывать их в одну нельзя: в реестре нашлись две записи
# «Неизвестно», которые слились бы в одну строку с чужими связями.
_PLACEHOLDER_KEYS = frozenset(
    {
        "неизвестно", "неуказано", "неопределено", "неприменимо",
        "безимени", "unknown", "notspecified", "notapplicable", "none",
        "na", "nan", "supplier", "поставщик", "manufacturer",
    }
)


def company_key(name: str) -> str | None:
    """Имя компании без регистра, разделителей и юридических хвостов.

    По нему одна компания, найденная на своём сайте и на двух площадках,
    остаётся одной строкой. Замер по реестру: 182 поставщика при 159
    различных компаниях — 23 лишние строки, Hangzhou Leap Chem четырьмя
    записями, Jiangsu Honon и TNJ Chemical тремя. Контакт при этом
    садился только на одну из них, и в таблице отбора остальные были
    бесполезны.
    """
    collapsed = _SEPARATORS_RE.sub("", name or "").casefold()
    changed = True
    while changed:
        changed = False
        for tail in _LEGAL_TAILS:
            if collapsed.endswith(tail) and len(collapsed) > len(tail) + 2:
                collapsed = collapsed[: -len(tail)]
                changed = True
    if len(collapsed) < _MIN_KEY_LENGTH or collapsed in _PLACEHOLDER_KEYS:
        return None
    return collapsed[:255]


def _is_platform_own_address(email: str, page_url: str, platforms: set[str]) -> bool:
    """Это адрес хозяина площадки, а не компании на её витрине.

    Витрина компании на площадке — законный источник: у Jiangsu Honon так
    нашёлся info@jshonon.com, у Qingdao Fuao — ethan@fuaochem.com, оба на
    собственных доменах. Но рядом на той же странице стоит почта самой
    площадки, и наполнение реестра приписало service@chemball.com сразу
    трём разным китайским заводам. Письмо ушло бы владельцу каталога.

    Правило действует только на страницах площадок. На собственном сайте
    компании почта и должна быть на его домене, и трогать её нельзя.
    """
    if not is_intermediary(page_url, platforms):
        return False
    page_label = domain_label(
        (urlparse(page_url if "//" in page_url else f"//{page_url}").hostname or "")
        .casefold()
    )
    mail_host = email.rpartition("@")[2].casefold()
    return bool(page_label) and bool(mail_host) and page_label in mail_host


def _attach_contacts(
    db: Session,
    *,
    supplier: Supplier,
    result: dict,
    substance: str,
) -> None:
    """Заводит контакты компании, снятые со страницы при загрузке.

    Без этого поставщик попадал в «Отобранные поставщики» без канала
    связи, а галочку в таблице поставить нельзя: канал берётся из
    контактов менеджера. То есть поиск доводил до компании и на этом
    останавливался, хотя почта и телефон лежали в уже загруженной
    странице — связь нашлась у 93 карточек из 136.

    Контакты страницы площадки не берутся: там указан отдел продаж самой
    площадки, а не компании, и письмо ушло бы не туда.
    """
    if result.get("supplier_type") == "marketplace":
        return
    contacts = result.get("contacts") or {}
    emails = [str(value).strip() for value in contacts.get("emails") or []]
    whatsapp = [str(value).strip() for value in contacts.get("whatsapp") or []]
    platforms = active_domains(db)
    page_url = str(result.get("url") or "")
    emails = [
        email
        for email in emails
        if not _is_platform_own_address(email, page_url, platforms)
    ]
    if not emails and not whatsapp:
        return

    # Читаем из базы, а не из supplier.managers: связь после вставки в той
    # же сессии остаётся прежней, и повторный прогон заводил контакт заново.
    existing = db.scalars(
        select(Manager).where(Manager.supplier_id == supplier.id)
    ).all()
    known_emails = {(manager.email or "").casefold() for manager in existing}
    known_whatsapp = {(manager.whatsapp or "").strip() for manager in existing}
    offered = [substance] if substance else None

    added = 0
    for email in emails:
        if added >= _MAX_MANAGERS or not email or email.casefold() in known_emails:
            continue
        db.add(
            Manager(
                supplier_id=supplier.id,
                email=email[:255],
                # WhatsApp приписывается первому контакту: страница даёт
                # один номер на компанию, а не на человека.
                whatsapp=(whatsapp[0][:64] if whatsapp and added == 0 else None),
                offered_substances=offered,
            )
        )
        known_emails.add(email.casefold())
        added += 1

    if added == 0 and whatsapp and whatsapp[0] not in known_whatsapp:
        # Почты нет, но написать всё равно есть куда.
        db.add(
            Manager(
                supplier_id=supplier.id,
                whatsapp=whatsapp[0][:64],
                offered_substances=offered,
            )
        )
    db.flush()


def register_qualified_candidate(
    db: Session,
    *,
    search_run: SearchRun,
    result: dict,
) -> Supplier | None:
    """Save a verified-page result as a candidate, never as an approved supplier."""
    if search_run.rfq_id is None:
        return None

    source_url = str(result.get("url") or "").strip()
    if not source_url:
        return None

    # Обзор рынка компанию не представляет. Отчёт «potassium sorbate
    # market» перечислял ведущих игроков, модель взяла оттуда имя Henan GP
    # Chemicals, а контакты снялись со страницы — в реестре появился
    # «Henan GP» с почтой исследовательского агентства, и письмо по ней
    # ушло бы не тому.
    if result.get("is_market_report"):
        return None

    stored_source = source_url[:255]
    company = str(
        result.get("company_name") or result.get("title") or source_url
    ).strip()
    key = company_key(company)

    supplier = db.scalar(
        select(Supplier).where(Supplier.source == stored_source).limit(1)
    )
    if supplier is None and key:
        # Та же компания, найденная на другой странице: на своём сайте, на
        # витрине площадки и в каталоге. Адрес разный, компания одна.
        supplier = db.scalar(
            select(Supplier)
            .where(Supplier.company_key == key)
            .order_by(Supplier.id)
            .limit(1)
        )
    supplier_kind = result.get("supplier_type")
    mapped_type = (
        SupplierType(supplier_kind)
        if supplier_kind in {"manufacturer", "distributor"}
        else None
    )
    score = result.get("confidence")
    evidence_score = score if isinstance(score, int) else None
    certificates = [
        label
        for field, label in (
            ("gmp_status", "GMP"),
            ("iso_status", "ISO"),
            ("coa_status", "CoA"),
            ("tds_status", "TDS"),
        )
        if result.get(field) == "claimed"
    ]

    if supplier is None:
        supplier = Supplier(
            company=company[:255],
            company_key=key,
            country=(search_run.input_payload or {}).get("country"),
            type=mapped_type,
            reputation=(
                f"Автоматическая квалификация: {evidence_score}/100; "
                "требуется решение специалиста"
            )[:255]
            if evidence_score is not None
            else "Автоматическая квалификация; требуется решение специалиста",
            source=stored_source,
            certificates=certificates or None,
            qualification_status="candidate",
            evidence_score=evidence_score,
            last_checked_at=utc_now(),
        )
        db.add(supplier)
        db.flush()
    else:
        # Записи, заведённые до появления ключа, получают его при первой же
        # встрече — иначе они так и останутся отдельными строками.
        if supplier.company_key is None and key:
            supplier.company_key = key
        if evidence_score is not None:
            supplier.evidence_score = max(
                supplier.evidence_score or 0, evidence_score
            )
        if supplier.type is None and mapped_type is not None:
            supplier.type = mapped_type
        if certificates:
            supplier.certificates = sorted(
                set(supplier.certificates or []).union(certificates)
            )
        supplier.last_checked_at = utc_now()

    _attach_contacts(
        db,
        supplier=supplier,
        result=result,
        substance=str((search_run.input_payload or {}).get("name") or "").strip(),
    )

    link = db.scalar(
        select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == search_run.rfq_id,
            RfqSupplierLink.supplier_id == supplier.id,
        )
    )
    if link is None:
        link = RfqSupplierLink(
            rfq_id=search_run.rfq_id,
            supplier_id=supplier.id,
            search_run_id=search_run.id,
            source_url=source_url,
            status="candidate",
        )
        db.add(link)
        db.flush()
    elif link.search_run_id is None:
        link.search_run_id = search_run.id

    return supplier
