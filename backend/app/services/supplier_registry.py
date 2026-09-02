"""Idempotent registration of AI-qualified supplier candidates."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Manager, RfqSupplierLink, SearchRun, Supplier
from app.models.enums import SupplierType
from app.services.intermediaries import (
    active_domains,
    domain_label,
    is_intermediary,
    known_domains,
)
from app.services.search_trace import utc_now

# Сколько контактов одной компании имеет смысл заводить. Больше — это уже
# не отдел продаж, а разбор чужого списка рассылки.
_MAX_MANAGERS = 3

# Роды страниц, которые компании не принадлежат: обзор рынка, научная
# статья, справочник, перечень площадки. Компанию такая страница называет,
# но говорит о ней с чужих слов, и контакты на ней — чужие. Держится
# здесь, а не импортируется из api: реестр не должен зависеть от слоя
# запросов.
NOT_THE_COMPANYS_OWN_PAGE = frozenset(
    {"market_report", "scientific", "directory", "marketplace_listing"}
)

# Модель, не сумев назвать компанию, пишет заглушку: «Не определено
# (список производителей)», «Не указана (платформа ECHEMI)». Замер по 1079
# сохранённым карточкам: 28 таких имён, все начинаются одинаково.
_NO_COMPANY_NAMED_RE = re.compile(
    r"^\s*(не\s*определ|не\s*указан|не\s*применим|неизвест"
    r"|unknown|not\s+specified|not\s+applicable|n/?a)",
    re.IGNORECASE,
)


def names_a_company(name: str) -> bool:
    """Названа ли на странице компания, или там стоит заглушка.

    Заводить «Не определено (список производителей)» незачем: писать
    некому и звать эту строку никак. Всё остальное в реестр попадает —
    даже если роль или страна не определились.
    """
    return bool(name.strip()) and not _NO_COMPANY_NAMED_RE.match(name)

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


def found_on_someone_elses_page(result: dict) -> bool:
    """Найдена не на своём сайте: справочник, обзор рынка, статья, перечень.

    Имя такая страница называет верно, а вот почта на ней принадлежит
    владельцу сайта. Компанию заводим, контакты — нет.
    """
    return bool(
        result.get("is_market_report")
        or result.get("page_kind") in NOT_THE_COMPANYS_OWN_PAGE
    )


def register_marketplace_seller(
    db: Session,
    *,
    search_run: SearchRun,
    seller,
) -> Supplier | None:
    """Заводит продавца, названного площадкой в поисковой выдаче.

    Отдельно от register_qualified_candidate намеренно: здесь нет ни
    страницы компании, ни доказательств — только строка описания. Роль и
    страна записываются как сведения площадки, а не как проверенный факт,
    и балл не выставляется вовсе.

    Связи тоже нет: писать такому продавцу можно лишь через саму площадку,
    и в карточке это видно по contact_barrier.
    """
    if search_run.rfq_id is None:
        return None

    # Личность продавца здесь — имя, а не адрес страницы: одна и та же
    # ссылка на площадку может прийти с разными компаниями в описании, и
    # поиск по адресу склеил бы их в одну. Тест на повторный прогон это и
    # поймал.
    key = company_key(seller.company)
    supplier = (
        db.scalar(
            select(Supplier)
            .where(Supplier.company_key == key)
            .order_by(Supplier.id)
            .limit(1)
        )
        if key
        else None
    )

    if supplier is None:
        supplier = Supplier(
            company=seller.company[:255],
            company_key=key,
            country=seller.country,
            type=(
                SupplierType(seller.claimed_role)
                if seller.claimed_role in {"manufacturer", "distributor"}
                else None
            ),
            reputation=(
                f"Сведения площадки {seller.platform}: "
                f"{seller.claimed_role or 'роль не указана'}; "
                "страница компании недоступна, проверка не проводилась"
            )[:255],
            source=seller.listing_url[:255],
            qualification_status="candidate",
            contact_barrier="platform",
            last_checked_at=utc_now(),
        )
        db.add(supplier)
        db.flush()
    else:
        # Компания уже известна по собственному сайту — там сведений
        # больше, и затирать их площадкой нельзя.
        if supplier.country is None and seller.country:
            supplier.country = seller.country
        if supplier.company_key is None and key:
            supplier.company_key = key

    if search_run.rfq_id is None:
        return supplier

    link = db.scalar(
        select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == search_run.rfq_id,
            RfqSupplierLink.supplier_id == supplier.id,
        )
    )
    if link is None:
        db.add(
            RfqSupplierLink(
                rfq_id=search_run.rfq_id,
                supplier_id=supplier.id,
                search_run_id=search_run.id,
                source_url=seller.listing_url,
                status="candidate",
            )
        )
        db.flush()
    return supplier


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
    own_page: bool = True,
) -> None:
    """Заводит контакты компании, снятые со страницы при загрузке.

    Без этого поставщик попадал в «Отобранные компании» без канала
    связи, а галочку в таблице поставить нельзя: канал берётся из
    контактов менеджера. То есть поиск доводил до компании и на этом
    останавливался, хотя почта и телефон лежали в уже загруженной
    странице — связь нашлась у 93 карточек из 136.

    Контакты страницы площадки не берутся: там указан отдел продаж самой
    площадки, а не компании, и письмо ушло бы не туда.
    """
    if result.get("supplier_type") == "marketplace":
        return
    if not own_page:
        # Страница чужая, и почта на ней чужая: у справочника patenthub.cn
        # это caoxd@patenthub.cn, у журнала об масличных культурах —
        # адрес редакции. Компанию в списке показываем, канал связи
        # закупщик добавит сам, открыв её сайт.
        # Читаем из базы, а не из supplier.managers: связь после вставки в
        # той же сессии остаётся прежней.
        has_contact = db.scalar(
            select(Manager.id).where(Manager.supplier_id == supplier.id).limit(1)
        )
        if not has_contact and not supplier.contact_barrier:
            supplier.contact_barrier = "third_party"
        return
    contacts = result.get("contacts") or {}
    emails = [str(value).strip() for value in contacts.get("emails") or []]
    whatsapp = [str(value).strip() for value in contacts.get("whatsapp") or []]
    # Весь реестр, а не только действующие записи: выключение говорит
    # «не выбрасывай эти ссылки», а не «доверяй их почтовому ящику».
    platforms = known_domains(db)
    page_url = str(result.get("url") or "")
    emails = [
        email
        for email in emails
        if not _is_platform_own_address(email, page_url, platforms)
    ]
    if not emails and not whatsapp:
        # Связи нет — но причина бывает разной, и закупщику её надо знать:
        # скрытый адрес значит «напиши, открыв страницу руками», а форма —
        # «другого пути нет».
        barrier = result.get("contact_barrier")
        if barrier and not supplier.contact_barrier:
            supplier.contact_barrier = str(barrier)[:32]
        return
    supplier.contact_barrier = None

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


# Сила подтверждения страны. Прямое заявление компании о себе весомее
# косвенного признака вроде доменной зоны, а тот — молчания страницы.
_COUNTRY_RANK = {"claimed": 3, "likely": 2, "not_found": 1}


def _country_rank(status: object) -> int:
    return _COUNTRY_RANK.get(str(status or ""), 0)


def _country_quote(result: dict) -> str | None:
    """Дословная цитата, на которой держится страна компании."""
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("claim_type") != "country":
            continue
        if item.get("quote_verified") is not True:
            continue
        quote = str(item.get("quote") or "").strip()
        if quote:
            return quote[:500]
    return None


def register_qualified_candidate(
    db: Session,
    *,
    search_run: SearchRun,
    result: dict,
) -> Supplier | None:
    """Save a verified-page result as a candidate, never as an approved supplier.

    Заявка больше не обязательна. Раньше прогон без неё не заводил в реестр
    ничего, и реестр рос с малой доли запусков: компания, найденная в
    свободном поиске, забывалась к следующему разу, и её приходилось
    находить заново за десяток запросов. Связь с заявкой по-прежнему
    создаётся только там, где заявка есть.
    """
    source_url = str(result.get("url") or "").strip()
    if not source_url:
        return None

    stored_source = source_url[:255]
    company = str(
        result.get("company_name") or result.get("title") or source_url
    ).strip()

    # Компанию, найденную поиском, из списка не выбрасываем: закупщик
    # сравнил число в «Найденных компаниях» с числом в «Отобранных» и
    # справедливо спросил, куда делись остальные. Не определились роль или
    # страна — это повод показать строку с оговоркой, а не спрятать её.
    #
    # Кроме случая, когда компании на странице нет вовсе: «Не определено
    # (список производителей)» — не имя, писать по нему некому.
    if not names_a_company(company):
        return None

    # Страница компании не принадлежит: обзор рынка, справочник, перечень
    # площадки. Имя оттуда берём, контакты — никогда. Отчёт «potassium
    # sorbate market» перечислял ведущих игроков, модель взяла оттуда имя
    # Henan GP Chemicals, а контакты снялись со страницы — в реестре
    # появился «Henan GP» с почтой исследовательского агентства, и письмо
    # ушло бы не тому. Прогон 281 тем же путём завёл со страницы PubMed
    # «компанию» с личной почтой исследователя.
    own_page = not found_on_someone_elses_page(result)

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

    requested_country = (search_run.input_payload or {}).get("country")
    country_status = result.get("country_status")
    country_status = (
        country_status
        if country_status in {"claimed", "likely", "not_found"}
        else None
    )
    country_evidence = _country_quote(result)
    raw_licence = str(result.get("icp_licence") or "").strip()
    icp_licence = raw_licence[:64] or None
    # При mismatch страница прямо назвала другую страну. Записать сюда
    # страну поиска значило бы записать заведомо неверное.
    evidenced_country = (
        requested_country if result.get("country_status") != "mismatch" else None
    )

    if supplier is None:
        supplier = Supplier(
            company=company[:255],
            company_key=key,
            country=evidenced_country,
            country_status=country_status,
            country_evidence=country_evidence,
            icp_licence=icp_licence,
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
        # Подтверждение сильнее умолчания: «прямо указано» вытесняет
        # «косвенно», а то — «страница не сказала ничего». Обратно статус
        # не понижается: один неудачно загруженный источник не отменяет
        # уже прочитанной цитаты.
        # Лицензия не меняется у компании, поэтому записывается один раз
        # и не перетирается пустотой с другой её страницы.
        if supplier.icp_licence is None and icp_licence:
            supplier.icp_licence = icp_licence
        if _country_rank(country_status) > _country_rank(supplier.country_status):
            supplier.country_status = country_status
            supplier.country_evidence = country_evidence
            if evidenced_country:
                supplier.country = evidenced_country
        elif supplier.country is None and evidenced_country:
            supplier.country = evidenced_country
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
        own_page=own_page,
    )

    if search_run.rfq_id is None:
        return supplier

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
