"""Прикладной сервис RFQ (L2): создание запроса с верификацией и генерацией.

Оркеструет шаги функций 1–2 ТЗ: приём входных данных → верификация вещества
по CAS → генерация стандартизированного RFQ → сохранение со статусом.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.pubchem import PubChemConnector
from app.models.enums import RFQStatus
from app.models.rfq import RFQ
from app.models.search_trace import SearchRun
from app.schemas.rfq import RFQCreate
from app.services.rfq_builder import RFQInput, build_rfq
from app.services.search_trace import cancel_search_run, utc_now

if TYPE_CHECKING:
    from app.models.substance import Substance


def create_rfq(
    db: Session,
    data: RFQCreate,
    *,
    verify: bool = True,
    owner_id: int | None = None,
    commit: bool = True,
) -> RFQ:
    """Создаёт и сохраняет RFQ.

    Валидирует базисы (через build_rfq), при verify=True проверяет вещество
    по CAS (PubChem) и проставляет статус VERIFIED/DRAFT.

    `commit=False` оставляет запись в текущей транзакции. Так её создаёт
    пакет: каждая позиция там заворачивается в свою точку сохранения, и
    коммит внутри отдельной позиции закрыл бы транзакцию всего пакета —
    откатить одну неудачную строку стало бы нечем.
    """
    # Валидация базисов выполняется здесь (бросит UnsupportedIncotermError).
    build_rfq(
        RFQInput(
            cas=data.cas,
            name=data.name,
            identification_method=data.identification_method,
            analog_reference=data.analog_reference,
            analog_variations=list(data.analog_variations),
            specification=data.specification,
            incoterms=data.incoterms,
            purity=data.purity,
            application=data.application,
            volume=data.volume,
            target_price=data.target_price,
            currency=data.currency,
        )
    )

    verification = None
    verified = False
    status = RFQStatus.DRAFT
    field_sources: dict[str, str] = {}
    # Проверять нечего, если номера нет: запрос по аналогу или
    # спецификации — не повод дёргать PubChem.
    if verify and data.cas:
        info = PubChemConnector().verify_cas(data.cas)
        verification = info.as_dict()
        verified = info.found
        status = RFQStatus.VERIFIED if info.found else RFQStatus.DRAFT
        if info.found:
            field_sources["cas"] = "pubchem"
    if data.cas and not verified:
        # Номер ввёл человек, а подтверждения не получил. Источник
        # фиксируем честно: это не справочные данные.
        field_sources["cas"] = "human"

    rfq = RFQ(
        cas=data.cas,
        name=data.name,
        identification_method=data.identification_method,
        analog_reference=data.analog_reference,
        analog_variations=list(data.analog_variations) or None,
        specification=data.specification,
        confirmed_synonyms=list(data.confirmed_synonyms) or None,
        excluded_names=list(data.excluded_names) or None,
        field_sources=field_sources or None,
        purity=data.purity,
        application=data.application,
        volume=data.volume,
        target_price=data.target_price,
        currency=data.currency,
        specialist_comment=data.specialist_comment,
        incoterms=[i.strip().upper() for i in data.incoterms],
        channels=data.channels or [],
        search_countries=data.search_countries,
        supplier_target=data.supplier_target,
        substance_id=data.substance_id,
        status=status,
        verified=verified,
        verification=verification,
        owner_id=owner_id,
    )
    db.add(rfq)
    if commit:
        db.commit()
        db.refresh(rfq)
    else:
        db.flush()
    return rfq


def render_rfq_text(rfq: RFQ) -> tuple[str, str]:
    """Возвращает ручной черновик или генерирует RFQ из сохранённой записи."""
    if rfq.rfq_subject_override and rfq.rfq_body_override:
        return rfq.rfq_subject_override, rfq.rfq_body_override

    result = build_rfq(
        RFQInput(
            cas=rfq.cas,
            name=external_rfq_name(rfq),
            identification_method=rfq.identification_method,
            analog_reference=rfq.analog_reference,
            analog_variations=list(rfq.analog_variations or []),
            specification=rfq.specification,
            incoterms=list(rfq.incoterms or []),
            purity=rfq.purity,
            application=rfq.application,
            volume=rfq.volume,
            target_price=float(rfq.target_price) if rfq.target_price else None,
            currency=rfq.currency or "USD",
        ),
        # Запрос уже сохранён, и справочник базисов с тех пор мог измениться.
        # Строгая проверка здесь роняла бы саму карточку: закупщик не смог бы
        # открыть собственный отправленный запрос из-за того, что базис
        # переименовали. Показываем сохранённое как есть.
        strict=False,
    )
    return result["subject"], result["body"]


def external_rfq_name(rfq: RFQ) -> str:
    """Выбирает подтверждённое латинское название для внешнего письма.

    Русское название остаётся в карточке и аудите. Если PubChem не подтвердил
    англоязычный вариант, система ничего не переводит и не придумывает.
    """
    name = (rfq.name or "").strip()
    if name.isascii():
        return name
    verification = rfq.verification or {}
    candidates = [
        item.strip()
        for item in verification.get("synonyms") or []
        if isinstance(item, str) and item.strip()
    ]
    iupac = verification.get("iupac_name")
    if isinstance(iupac, str) and iupac.strip():
        candidates.append(iupac.strip())

    def usable(value: str) -> bool:
        return (
            value.isascii()
            and 2 < len(value) <= 120
            and any(char.isalpha() for char in value)
            and not value.replace("-", "").isdigit()
        )

    usable_names = list(dict.fromkeys(item for item in candidates if usable(item)))
    if not usable_names:
        return name
    acid_names = [item for item in usable_names if "acid" in item.casefold()]
    return min(acid_names or usable_names, key=len)


def update_rfq_message_draft(
    db: Session,
    rfq: RFQ,
    *,
    subject: str | None,
    body: str | None,
) -> RFQ:
    """Сохраняет ручной RFQ или очищает его, возвращая единый шаблон."""
    rfq.rfq_subject_override = subject
    rfq.rfq_body_override = body
    db.commit()
    db.refresh(rfq)
    return rfq


def archive_rfq(
    db: Session,
    rfq: RFQ,
    *,
    actor_id: int,
) -> RFQ:
    """Soft-delete a request and stop its active background searches."""
    if rfq.deleted_at is not None:
        return rfq

    reason = "Поиск остановлен: связанный запрос удалён пользователем."
    active_runs = list(
        db.scalars(
            select(SearchRun)
            .where(
                SearchRun.rfq_id == rfq.id,
                SearchRun.status.not_in({"completed", "failed", "cancelled"}),
            )
            .options(
                selectinload(SearchRun.agent_runs),
                selectinload(SearchRun.search_attempts),
                selectinload(SearchRun.source_documents),
            )
        ).all()
    )
    for search_run in active_runs:
        cancel_search_run(search_run, reason=reason)

    rfq.deleted_at = utc_now()
    rfq.deleted_by_id = actor_id
    db.commit()
    return rfq


def merge_names(*groups: list[str] | None) -> list[str]:
    """Объединяет списки названий без повторов, сохраняя порядок."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or []:
            name = raw.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                merged.append(name)
    return merged


def search_run_payload(
    rfq: RFQ,
    *,
    country: str,
    substance: "Substance | None" = None,
    additional_instructions: str | None = None,
) -> dict:
    """Вход поискового прогона по одной позиции и одной стране.

    Вынесено из обработчика создания: пакетное создание ставит такие же
    прогоны, и собирать этот словарь во второй раз значит гарантировать
    расхождение. Однажды так уже вышло — кнопка «создать и начать поиск»
    строила payload вручную и теряла способ идентификации вместе с
    эталоном аналога, отчего поиск аналога тихо шёл как обычный.
    """
    return {
        "cas": rfq.cas,
        "name": rfq.name,
        "catalog_preferred_name": substance.preferred_name if substance else None,
        # Отметки закупщика по этому запросу идут вместе с накопленными в
        # карточке: без CAS-номера якорем поиска служит название, и именно
        # подтверждённые названия держат точность в этой ветке.
        "known_synonyms": merge_names(
            substance.synonyms if substance else None,
            rfq.confirmed_synonyms,
        ),
        "excluded_names": merge_names(
            substance.excluded_names if substance else None,
            rfq.excluded_names,
        ),
        "catalog_notes": substance.notes if substance else None,
        "country": country,
        "identification_method": rfq.identification_method,
        "analog_reference": rfq.analog_reference,
        "analog_variations": list(rfq.analog_variations or []),
        "specification": rfq.specification,
        "application": rfq.application,
        "requested_volume": rfq.volume,
        "additional_instructions": (
            additional_instructions.strip() if additional_instructions else None
        ),
        "limit": rfq.supplier_target,
    }
