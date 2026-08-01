"""Аренда задач очереди поиска для нескольких worker-процессов.

Одиночный worker мог считать любую незавершённую задачу своей: больше её
выполнять было некому. С репликами это перестаёт быть верным, поэтому у
задачи появляется владелец, срок аренды и номер поколения.

Три правила, которые обеспечивают безопасность масштабирования:

1. Задачу берёт только тот, у кого аренды нет или она просрочена. На
   PostgreSQL отбор идёт под ``FOR UPDATE SKIP LOCKED``.
2. Пока worker работает, он продлевает аренду фоновым heartbeat: длинные
   этапы (загрузка страниц, вызов модели) блокируют основной поток надолго.
3. Записывать результат разрешено только владельцу актуального поколения.
   Зависший worker, чья аренда была перевыдана, получает :class:`LeaseLost`
   вместо перезаписи чужого результата.
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import SearchRun
from app.services.search_trace import utc_now


def lease_ttl_s(explicit: float | None = None) -> float:
    """Срок аренды. Значение из настроек, если вызывающий не задал своё."""
    if explicit is not None:
        return explicit
    return float(get_settings().search_lease_ttl_s)


def heartbeat_interval_s(explicit: float | None = None) -> float:
    """Период продления аренды с запасом относительно её срока."""
    if explicit is not None:
        return explicit
    return float(get_settings().search_lease_heartbeat_s)


class LeaseLost(RuntimeError):
    """Аренда потеряна: задачу уже ведёт другой worker."""


def worker_identity() -> str:
    """Устойчивый в пределах процесса идентификатор worker."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def grant_lease(
    run: SearchRun, owner: str, *, ttl_s: float | None = None
) -> int:
    """Выдаёт аренду и возвращает новое поколение (fencing token)."""
    run.lease_owner = owner
    run.lease_generation = int(run.lease_generation or 0) + 1
    run.lease_expires_at = utc_now() + timedelta(seconds=lease_ttl_s(ttl_s))
    return run.lease_generation


def release_lease(run: SearchRun) -> None:
    """Снимает аренду с завершённой задачи, сохраняя номер поколения."""
    run.lease_owner = None
    run.lease_expires_at = None


def renew_lease(
    db: Session,
    run_id: int,
    owner: str,
    generation: int,
    *,
    ttl_s: float | None = None,
) -> bool:
    """Продлевает аренду. False означает, что задачу забрал другой worker."""
    result = db.execute(
        update(SearchRun)
        .where(
            SearchRun.id == run_id,
            SearchRun.lease_owner == owner,
            SearchRun.lease_generation == generation,
        )
        .values(
            lease_expires_at=utc_now() + timedelta(seconds=lease_ttl_s(ttl_s))
        )
    )
    db.commit()
    return bool(result.rowcount)


def holds_lease(run: SearchRun, owner: str, generation: int) -> bool:
    """Владеет ли worker актуальной арендой этой задачи."""
    return (
        run.lease_owner == owner
        and int(run.lease_generation or 0) == generation
    )


def require_lease(run: SearchRun, owner: str, generation: int) -> None:
    """Бросает :class:`LeaseLost`, если аренда перевыдана другому worker."""
    if not holds_lease(run, owner, generation):
        raise LeaseLost(
            f"Аренда задачи {run.id} потеряна: владелец {run.lease_owner!r}, "
            f"поколение {run.lease_generation} вместо {generation}"
        )


def lease_is_recoverable(run: SearchRun, owner: str) -> bool:
    """Можно ли считать задачу брошенной.

    Брошенной считается задача без аренды, с истёкшей арендой или с арендой
    этого же worker до его перезапуска. Живая чужая аренда неприкосновенна.
    """
    if run.lease_owner is None or run.lease_owner == owner:
        return True
    expires_at = run.lease_expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        return expires_at < utc_now().replace(tzinfo=None)
    return expires_at < utc_now()


class LeaseHeartbeat:
    """Фоновое продление аренды на время длинных этапов.

    Этап может блокировать поток на минуты — загрузка страниц и вызов модели
    синхронные. Без heartbeat аренда истекла бы прямо во время работы, и
    задачу подхватил бы второй worker.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        run_id: int,
        owner: str,
        generation: int,
        interval_s: float | None = None,
        ttl_s: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.run_id = run_id
        self.owner = owner
        self.generation = generation
        self.interval_s = heartbeat_interval_s(interval_s)
        self.ttl_s = lease_ttl_s(ttl_s)
        self.renewals = 0
        self.lost = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread = threading.Thread(
            target=self._loop, name=f"lease-{self.run_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            with self.session_factory() as db:
                renewed = renew_lease(
                    db,
                    self.run_id,
                    self.owner,
                    self.generation,
                    ttl_s=self.ttl_s,
                )
            if not renewed:
                # Аренду перевыдали. Продолжать бессмысленно: запись
                # результата всё равно будет отклонена fencing-проверкой.
                self.lost = True
                return
            self.renewals += 1
