"""Общий на все процессы лимит обращений к внешнему домену.

Разные вещества приводят на разные сайты поставщиков, но два адреса
запрашивает каждый поиск независимо от вещества: поисковая выдача и PubChem.
Лимиты внешних сервисов действуют на домен и исходящий IP, а не на текст
запроса, поэтому при нескольких worker-процессах частота к этим двум хостам
растёт кратно числу реплик.

Слот хранится в PostgreSQL рядом с очередью: координация задач уже живёт
там, и заводить ради пауз второй координатор незачем. Нагрузка ничтожна —
одна короткая запись на исходящий запрос.

Отказ базы не должен останавливать поиск: при ошибке модуль возвращается к
паузе в памяти процесса, то есть к прежнему поведению, и сообщает об этом в
лог один раз.
"""

from __future__ import annotations

import logging
import threading
from time import monotonic, time
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import DomainRateSlot

logger = logging.getLogger(__name__)

# Общее хранилище может быть недоступно не только из-за ошибки соединения:
# в окружении без драйвера базы её модуль не импортируется вовсе. И то и
# другое означает одно — работаем по паузе в памяти процесса.
_STORAGE_ERRORS = (SQLAlchemyError, ImportError)

DEFAULT_INTERVAL_S = 1.0
# Максимальная пауза: слишком строгий лимит домена не должен останавливать
# этап целиком, у него есть собственный бюджет времени.
MAX_WAIT_S = 30.0

# Хосты, к которым обращается каждый поиск. Значения консервативные: их
# соблюдение дешевле, чем разбор блокировки по IP.
HOST_INTERVALS: dict[str, float] = {
    # Поисковая выдача: до 12 запросов на один поиск.
    "html.duckduckgo.com": 2.0,
    "duckduckgo.com": 2.0,
    # PubChem просит не превышать пять запросов в секунду на организацию и
    # сам применяет динамический троттлинг. Берём запас.
    "pubchem.ncbi.nlm.nih.gov": 0.34,
}


def _default_session_factory() -> sessionmaker[Session]:
    """Фабрика сессий разрешается при вызове, а не при импорте.

    Модуль вызывается из коннекторов, и связывать их импорт с созданием
    движка базы незачем: коннектор должен оставаться проверяемым без БД.
    """
    from app.core.db import SessionLocal

    return SessionLocal


_lock = threading.RLock()
_local_slots: dict[str, float] = {}
_degraded = False


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def interval_for(host: str) -> float:
    """Минимальный промежуток между обращениями к хосту."""
    return HOST_INTERVALS.get(host, DEFAULT_INTERVAL_S)


def _reserve_locally(host: str, interval: float) -> float:
    """Резерв в памяти процесса — запасной путь при недоступной базе."""
    with _lock:
        now = monotonic()
        start = max(_local_slots.get(host, now), now)
        _local_slots[host] = start + interval
        return start - now


def _reserve_in_db(db: Session, host: str, interval: float) -> float:
    """Занимает очередь к хосту в общем хранилище.

    Первое обращение к домену — гонка на вставку: строки ещё нет, поэтому
    блокировка строки её не защищает, и параллельные процессы попытаются
    создать её одновременно. Проигравший повторяет попытку, когда строка уже
    существует, и дальше идёт обычным путём с блокировкой.
    """
    for _ in range(2):
        slot = db.get(DomainRateSlot, host, with_for_update=True)
        now = time()
        if slot is not None:
            start = max(slot.next_allowed_at, now)
            slot.next_allowed_at = start + interval
            db.commit()
            return start - now
        try:
            db.add(DomainRateSlot(host=host, next_allowed_at=now + interval))
            db.commit()
            return 0.0
        except IntegrityError:
            db.rollback()
            db.expunge_all()
    raise IntegrityError("domain slot", {}, Exception("повторная вставка слота"))


def reserve_slot(
    url: str,
    interval_s: float | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> float:
    """Занимает очередь к домену и возвращает, сколько секунд ждать.

    Ожидание выполняет вызывающий: так функция остаётся проверяемой без
    задержек в тестах.
    """
    global _degraded
    host = host_of(url)
    if not host:
        return 0.0
    interval = interval_s if interval_s is not None else interval_for(host)
    try:
        factory = session_factory or _default_session_factory()
        # Внутрипроцессная блокировка поверх блокировки строки: потоки одного
        # worker не должны читать одно и то же значение слота и записывать
        # его наперегонки. Между процессами это обеспечивает PostgreSQL через
        # FOR UPDATE; у SQLite в dev строчных блокировок нет.
        with _lock:
            with factory() as db:
                wait = _reserve_in_db(db, host, interval)
        if _degraded:
            logger.info("Общий лимит домена снова работает через базу")
            _degraded = False
    except _STORAGE_ERRORS as exc:
        if not _degraded:
            logger.warning(
                "Общий лимит домена недоступен (%s); соблюдаю паузу только "
                "в этом процессе",
                exc,
            )
            _degraded = True
        wait = _reserve_locally(host, interval)
    return max(0.0, min(wait, MAX_WAIT_S))


def defer_domain(
    url: str,
    delay_s: float,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Отодвигает домен после 429/503 с Retry-After.

    Сервис прямо сказал, когда к нему можно вернуться. Отметку видят все
    процессы, поэтому соседний worker не продолжит стучаться в тот же хост.
    """
    host = host_of(url)
    if not host or delay_s <= 0:
        return
    delay = min(delay_s, MAX_WAIT_S)
    try:
        factory = session_factory or _default_session_factory()
        # Та же гонка на вставку, что и при выдаче слота, и та же защита.
        with _lock, factory() as db:
            slot = db.get(DomainRateSlot, host, with_for_update=True)
            resume_at = time() + delay
            if slot is None:
                try:
                    db.add(DomainRateSlot(host=host, next_allowed_at=resume_at))
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    db.expunge_all()
                    slot = db.get(DomainRateSlot, host, with_for_update=True)
            if slot is not None:
                slot.next_allowed_at = max(slot.next_allowed_at, resume_at)
                db.commit()
    except _STORAGE_ERRORS:
        with _lock:
            _local_slots[host] = max(
                _local_slots.get(host, 0.0), monotonic() + delay
            )


def retry_after_seconds(value: str | None) -> float:
    """Разбирает заголовок Retry-After в секундах. 0 — заголовка нет."""
    if not value:
        return 0.0
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        # Формат HTTP-date встречается реже; точная дата здесь не нужна,
        # достаточно безопасной паузы по умолчанию.
        return DEFAULT_INTERVAL_S


def reset_state() -> None:
    """Сбрасывает состояние в памяти процесса (используется в тестах)."""
    global _degraded
    with _lock:
        _local_slots.clear()
        _degraded = False
