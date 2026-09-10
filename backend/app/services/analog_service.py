"""Хранение подобранных аналогов и заведение запросов по выбранным.

Подбор (`analog_candidates`) ничего не сохраняет и ничего не создаёт. Этот
модуль отвечает за вторую половину: положить кандидатов рядом с запросом,
запомнить выбор закупщика и завести по каждому выбранному аналогу
собственный запрос с собственным поиском.

Отдельный запрос на каждый аналог, а не один общий: у поиска по каждому
веществу своя выдача, свои поставщики и своя сводка. Свалив их в один
запрос, мы получили бы список компаний, про который непонятно, кто из них
что именно продаёт, — а это ровно тот вопрос, ради которого замену и
подбирали.
"""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RFQ, RfqAnalogCandidate
from app.services.analog_candidates import AnalogSuggestion
from app.services.rfq_batch_service import BatchResult, create_rfq_batch
from app.services.search_trace import utc_now


def stored_candidates(db: Session, rfq_id: int) -> list[RfqAnalogCandidate]:
    """Подобранные аналоги в порядке подбора."""
    return list(
        db.scalars(
            select(RfqAnalogCandidate)
            .where(RfqAnalogCandidate.rfq_id == rfq_id)
            .order_by(RfqAnalogCandidate.id)
        )
    )


def store_suggestion(
    db: Session, rfq: RFQ, suggestion: AnalogSuggestion
) -> list[RfqAnalogCandidate]:
    """Кладёт результат подбора рядом с запросом.

    Повторный подбор заменяет прежних кандидатов, но не трогает тех, по
    которым запрос уже заведён: удалив их, мы потеряли бы объяснение,
    откуда взялся существующий запрос.
    """
    for candidate in stored_candidates(db, rfq.id):
        if candidate.created_rfq_id is None:
            db.delete(candidate)
    db.flush()

    known = {
        candidate.name.casefold(): candidate
        for candidate in stored_candidates(db, rfq.id)
    }
    created: list[RfqAnalogCandidate] = []
    for item in suggestion.candidates:
        if item.name.casefold() in known:
            # Тот же аналог уже заведён запросом — второй раз не предлагаем.
            continue
        candidate = RfqAnalogCandidate(
            rfq_id=rfq.id,
            name=item.name,
            cas=item.cas,
            cas_confirmed=item.cas_confirmed,
            reason=item.reason,
            quote=item.quote,
            source_url=item.source_url,
        )
        db.add(candidate)
        created.append(candidate)

    rfq.analog_suggested_at = utc_now()
    rfq.analog_warnings = list(suggestion.warnings)
    db.flush()
    return created


def _idempotency_key(rfq_id: int, candidate_ids: list[int]) -> str:
    """Один и тот же выбор не заводит второй набор запросов.

    Ключ считается по запросу и составу выбора: повторное нажатие и повтор
    после обрыва ответа приходят с тем же ключом. Добавленный к выбору
    аналог даёт другой ключ — и новый пакет, что и требуется.
    """
    digest = sha256(
        f"analog:{rfq_id}:{','.join(str(i) for i in sorted(candidate_ids))}".encode()
    ).hexdigest()
    return f"analog-{rfq_id}-{digest[:24]}"


def _child_values(parent: RFQ, candidate: RfqAnalogCandidate) -> dict:
    """Запрос по аналогу: своё вещество, условия закупки — родительские.

    Номер подставляется только подтверждённый: неподтверждённый увёл бы
    поиск к другому веществу молча. Без номера запрос идёт спецификацией —
    ровно так же, как обычная позиция без CAS.
    """
    cas = candidate.cas if candidate.cas_confirmed else None
    note_parts = [f"Аналог для «{parent.name}» (запрос №{parent.id})."]
    if candidate.reason:
        note_parts.append(f"Обоснование подбора: {candidate.reason}")
    if candidate.source_url:
        note_parts.append(f"Источник: {candidate.source_url}")
    if parent.specialist_comment:
        note_parts.append(parent.specialist_comment)

    return {
        "identification_method": "cas" if cas else "spec",
        "name": candidate.name,
        "cas": cas,
        # Требования к материалу остаются родительскими: заменяют вещество,
        # а не задачу, под которую его закупают.
        "specification": parent.specification,
        "purity": parent.purity,
        "application": parent.application,
        "volume": parent.volume,
        "target_price": float(parent.target_price)
        if parent.target_price is not None
        else None,
        "currency": parent.currency or "USD",
        "target_price_unit": parent.target_price_unit,
        "target_price_incoterm": parent.target_price_incoterm,
        "incoterms": list(parent.incoterms or []),
        "channels": list(parent.channels or []),
        "search_countries": list(parent.search_countries or ["Китай"]),
        "supplier_target": parent.supplier_target or 5,
        # Внутренняя заметка: в письмо поставщику не уходит. Через месяц
        # без неё непонятно, откуда взялся запрос на это вещество.
        "specialist_comment": "\n".join(note_parts)[:4000],
    }


def confirm_analogs(
    db: Session,
    rfq: RFQ,
    *,
    candidate_ids: list[int],
    owner_id: int,
    start_search: bool = True,
) -> BatchResult | None:
    """Заводит по запросу на каждый выбранный аналог и ставит поиски.

    Выбор запоминается на кандидатах: карточка исходной позиции должна
    показывать, что именно закупщик счёл подходящим, даже если заведённый
    запрос потом удалили.

    Возвращает None, если по всему выбранному запросы уже заведены: тогда
    менялись только отметки, и пакета не появилось.
    """
    candidates = {item.id: item for item in stored_candidates(db, rfq.id)}
    chosen = [candidates[item_id] for item_id in candidate_ids if item_id in candidates]
    for candidate in candidates.values():
        candidate.selected = candidate.id in {item.id for item in chosen}

    pending = [item for item in chosen if item.created_rfq_id is None]
    if not pending:
        # Всё выбранное уже заведено: отметки обновили, создавать нечего.
        # Пустой пакет здесь был бы враньём — его никто не создавал.
        db.flush()
        return None

    result = create_rfq_batch(
        db,
        owner_id=owner_id,
        idempotency_key=_idempotency_key(rfq.id, [item.id for item in pending]),
        source_name=f"Аналоги для «{rfq.name}»",
        items=[
            (index, _child_values(rfq, candidate))
            for index, candidate in enumerate(pending, start=1)
        ],
        start_search=start_search,
    )

    # Связь «кандидат -> заведённый запрос» по номеру строки: пакет вернул
    # результаты в том же порядке, в каком получил позиции.
    by_row = {item.row: item for item in result.results}
    for index, candidate in enumerate(pending, start=1):
        created = by_row.get(index)
        if created is not None and created.rfq_id is not None:
            candidate.created_rfq_id = created.rfq_id
    db.flush()
    return result
