"""Прикладной сервис RFQ (L2): создание запроса с верификацией и генерацией.

Оркеструет шаги функций 1–2 ТЗ: приём входных данных → верификация вещества
по CAS → генерация стандартизированного RFQ → сохранение со статусом.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.pubchem import PubChemConnector
from app.models.enums import RFQStatus
from app.models.rfq import RFQ
from app.models.search_trace import SearchRun
from app.schemas.rfq import RFQCreate
from app.services.rfq_builder import RFQInput, build_rfq
from app.services.search_trace import cancel_search_run, utc_now


def create_rfq(
    db: Session,
    data: RFQCreate,
    *,
    verify: bool = True,
    owner_id: int | None = None,
) -> RFQ:
    """Создаёт и сохраняет RFQ.

    Валидирует базисы (через build_rfq), при verify=True проверяет вещество
    по CAS (PubChem) и проставляет статус VERIFIED/DRAFT.
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
    db.commit()
    db.refresh(rfq)
    return rfq


def render_rfq_text(rfq: RFQ) -> tuple[str, str]:
    """Генерирует (subject, body) RFQ из сохранённой записи."""
    result = build_rfq(
        RFQInput(
            cas=rfq.cas,
            name=rfq.name,
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
        )
    )
    return result["subject"], result["body"]


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
