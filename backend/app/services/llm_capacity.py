"""Ограничение одновременных обращений к локальной модели.

Зачем. У llama-server фиксированное число слотов, а воркеров намеренно
больше: половину времени поиск занимает загрузка страниц, и всё это время
GPU простаивает. Но обращения к модели этой арифметике не подчиняются —
третий одновременный вызов встаёт в очередь сервера и ждёт.

Замер на стенде: три параллельных поиска, два слота. Ожидание в очереди
превысило таймаут запроса, тайм-аут поднялся как «модель недоступна», этап
перезапустился, снова стал третьим — и так по кругу. Семь отказов подряд,
сорок четыре минуты без единого результата, при том что модель была жива и
обрабатывала запросы.

Поэтому место у модели выдаётся явно и ровно по числу слотов. Воркеры
по-прежнему работают параллельно на загрузке страниц, а ждут — здесь, где
ожидание дёшево и не приводит к отказу.

Слот хранится в PostgreSQL рядом с очередью: координация задач уже живёт
там. Отказ базы не должен останавливать поиск — при ошибке модуль
возвращается к семафору в памяти процесса, то есть к прежнему поведению.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from datetime import timedelta
from time import monotonic, sleep

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models.llm_slot import LlmSlot
from app.services.search_trace import utc_now

logger = logging.getLogger(__name__)

_STORAGE_ERRORS = (SQLAlchemyError, ImportError)

# Сколько мест у модели. По умолчанию совпадает с --parallel у
# llama-server; несовпадение вверх возвращает ту же очередь на сервере.
DEFAULT_SLOTS = 2

# Аренда места. Дольше самого длинного вызова, но конечна: процесс может
# умереть посреди обращения, и без срока место осталось бы занятым.
LEASE_TTL_S = 900.0

# Как часто проверять освободившееся место.
_POLL_INTERVAL_S = 2.0

_fallback_lock = threading.BoundedSemaphore(DEFAULT_SLOTS)
_session_factory: sessionmaker | None = None
_storage_warned = False


def configured_slots() -> int:
    """Число мест у модели."""
    raw = os.environ.get("LLM_PARALLEL_SLOTS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SLOTS
    except ValueError:
        value = DEFAULT_SLOTS
    return max(1, value)


def _default_session_factory() -> sessionmaker:
    """Фабрика сессий создаётся лениво: импорт движка в момент загрузки
    модуля ломает тесты, которые обходятся без базы."""
    global _session_factory
    if _session_factory is None:
        from app.core.db import SessionLocal

        _session_factory = SessionLocal
    return _session_factory


def ensure_slots(db: Session) -> int:
    """Приводит число строк к числу мест у модели."""
    slots = configured_slots()
    existing = list(db.scalars(select(LlmSlot).order_by(LlmSlot.id)).all())
    for _ in range(len(existing), slots):
        db.add(LlmSlot())
    for extra in existing[slots:]:
        db.delete(extra)
    db.commit()
    return slots


def _try_take(db: Session, owner: str) -> int | None:
    """Занимает свободное место, если оно есть."""
    now = utc_now()
    row = db.execute(
        select(LlmSlot)
        .where((LlmSlot.owner.is_(None)) | (LlmSlot.expires_at < now))
        .order_by(LlmSlot.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        db.rollback()
        return None
    row.owner = owner
    row.expires_at = now + timedelta(seconds=LEASE_TTL_S)
    db.commit()
    return row.id


def _release(db: Session, slot_id: int, owner: str) -> None:
    row = db.get(LlmSlot, slot_id)
    if row is not None and row.owner == owner:
        row.owner = None
        row.expires_at = None
        db.commit()


@contextmanager
def model_slot(owner: str, *, wait_s: float = 1800.0):
    """Занимает место у модели на время вызова.

    Ожидание здесь безопасно: место освобождается, как только предыдущий
    вызов закончился. Это противоположность ожиданию в очереди сервера,
    где превышение таймаута запроса означает отказ этапа.
    """
    global _storage_warned
    try:
        factory = _default_session_factory()
    except _STORAGE_ERRORS:
        factory = None

    if factory is None:
        with _fallback_lock:
            yield None
        return

    deadline = monotonic() + wait_s
    slot_id: int | None = None
    try:
        while slot_id is None:
            try:
                with factory() as db:
                    if _needs_setup(db):
                        ensure_slots(db)
                    slot_id = _try_take(db, owner)
            except _STORAGE_ERRORS as exc:
                if not _storage_warned:
                    logger.warning(
                        "Общий счётчик мест у модели недоступен (%s); "
                        "ограничиваю параллельность в памяти процесса",
                        exc.__class__.__name__,
                    )
                    _storage_warned = True
                with _fallback_lock:
                    yield None
                return
            if slot_id is None:
                if monotonic() >= deadline:
                    # Ждать дольше смысла нет: пусть этап отчитается о
                    # перегрузке, а не притворяется, что работает.
                    raise TimeoutError("нет свободного места у модели")
                sleep(_POLL_INTERVAL_S)
        yield slot_id
    finally:
        if slot_id is not None:
            try:
                with factory() as db:
                    _release(db, slot_id, owner)
            except _STORAGE_ERRORS:
                pass


def _needs_setup(db: Session) -> bool:
    """Есть ли вообще строки мест."""
    return db.scalar(select(LlmSlot.id).limit(1)) is None
