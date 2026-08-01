"""Краш-тест пропускной способности поиска поставщиков.

Отвечает на вопрос «сколько веществ можно искать одновременно»: разделяет три
независимых предела и измеряет каждый отдельно.

1. API. Постановка задачи в очередь — короткая транзакция без LLM, поэтому
   burst параллельных запросов ограничен только сервером и БД.
2. Worker. `app.search_worker` держит один слот выполнения, поэтому глубина
   очереди растёт, а нагрузка на модель — нет.
3. Локальная LLM. Реальный предел одновременных поисков равен числу слотов
   llama-server (`--parallel`). Воркеров больше, чем слотов, — это не рост
   пропускной способности, а таймауты и повторы.

Локальная модель здесь заменена детерминированной заглушкой
:class:`LocalModelStub` с настраиваемым числом слотов: тест не обращается к
GPU и не ходит в сеть. Измерение реального железа выполняет
`backend/scripts/search_load_test.py`.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import monotonic, sleep

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_search_load.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.base import PGDialect

from app import search_worker
from app.core.db import SessionLocal, engine
from app.extraction.llm_client import LLMUnavailableError
from app.main import app
from app.models import SearchRun
from app.search_worker import claim_next_job, process_next_job
from app.services.search_trace import utc_now

# Столько LLM-вызовов делает один запуск в текущем конвейере: идентификация
# вещества + планировщик запросов на этапе поиска, затем квалификация и
# проверка пакетами по два источника (_QUALIFICATION_BATCH_SIZE = 2).
LLM_CALLS_PER_SEARCH_PHASE = 2
LLM_CALLS_PER_QUALIFICATION_PHASE = 6
LLM_CALLS_PER_RUN = LLM_CALLS_PER_SEARCH_PHASE + LLM_CALLS_PER_QUALIFICATION_PHASE


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_search_load.db"):
        os.remove("test_search_load.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_search_load.db"):
        os.remove("test_search_load.db")


def _auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _synthetic_cas(index: int) -> str:
    """CAS с верной контрольной цифрой: запрос иначе отклоняется на 422."""
    body = f"{index:09d}"
    check = sum(int(d) * i for i, d in enumerate(reversed(body), start=1)) % 10
    return f"{body[:7]}-{body[7:]}-{check}"


class LocalModelStub:
    """Локальная модель с ограниченным числом слотов инференса.

    Повторяет поведение llama-server: запрос сверх `--parallel` не отклоняется
    сразу, а ждёт освобождения слота и падает по таймауту. Именно этот отказ
    воркер трактует как временную недоступность модели и возвращает задачу в
    очередь.
    """

    def __init__(
        self,
        *,
        slots: int = 1,
        call_seconds: float = 0.03,
        timeout_s: float = 2.0,
    ) -> None:
        self.slots = slots
        self.call_seconds = call_seconds
        self.timeout_s = timeout_s
        self._semaphore = threading.BoundedSemaphore(slots)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak_in_flight = 0
        self.calls = 0
        self.timeouts = 0

    def generate(self) -> None:
        if not self._semaphore.acquire(timeout=self.timeout_s):
            with self._lock:
                self.timeouts += 1
            raise LLMUnavailableError(
                "локальная модель не освободила слот за отведённое время"
            )
        try:
            with self._lock:
                self.calls += 1
                self.in_flight += 1
                self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            sleep(self.call_seconds)
        finally:
            with self._lock:
                self.in_flight -= 1
            self._semaphore.release()


@dataclass
class BurstReport:
    """Итог одного прогона: что выдержало, а что нет."""

    enqueued: int
    workers: int
    model_slots: int
    completed: int = 0
    failed: int = 0
    unfinished: int = 0
    wall_s: float = 0.0
    enqueue_wall_s: float = 0.0
    peak_model_concurrency: int = 0
    model_calls: int = 0
    model_timeouts: int = 0
    poll_requests: int = 0
    poll_errors: int = 0
    slowest_poll_s: float = 0.0
    statuses: dict[str, int] = field(default_factory=dict)

    @property
    def survived(self) -> bool:
        return (
            self.failed == 0
            and self.unfinished == 0
            and self.poll_errors == 0
            and self.completed == self.enqueued
        )

    def as_line(self) -> str:
        return (
            f"воркеров={self.workers} слотов модели={self.model_slots} "
            f"веществ={self.enqueued} -> завершено={self.completed} "
            f"провалено={self.failed} незавершено={self.unfinished} "
            f"пик модели={self.peak_model_concurrency} "
            f"таймаутов={self.model_timeouts} "
            f"время={self.wall_s:.2f}с"
        )


def _enqueue_burst(
    client: TestClient,
    headers: dict[str, str],
    *,
    count: int,
    parallelism: int,
    offset: int,
) -> tuple[list[dict], float]:
    """Одновременно ставит `count` веществ в очередь из `parallelism` потоков."""

    def enqueue(index: int) -> dict:
        response = client.post(
            "/supplier-search/jobs",
            headers=headers,
            json={
                "cas": _synthetic_cas(offset + index),
                "name": f"Load test substance {offset + index}",
                "country": "Китай",
            },
        )
        return {"status_code": response.status_code, "body": response.json()}

    started = monotonic()
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        responses = list(pool.map(enqueue, range(count)))
    return responses, monotonic() - started


def _make_pipeline(model: LocalModelStub):
    """Заглушки этапов конвейера с реальным профилем обращений к модели."""

    def executor(data, db, user, *, search_run):
        for _ in range(LLM_CALLS_PER_SEARCH_PHASE):
            model.generate()
        return {
            "search_run_id": search_run.id,
            "query": f'"{data.cas}" manufacturer',
            "queries_used": [f'"{data.cas}" manufacturer'],
            "results": [
                {
                    "title": f"{data.name} manufacturer",
                    "url": f"https://supplier-{search_run.id}.example/product",
                    "snippet": f"We manufacture {data.name}, CAS {data.cas}.",
                    "country_hint": "likely",
                }
            ],
        }

    def qualifier(data, db, user):
        for _ in range(LLM_CALLS_PER_QUALIFICATION_PHASE):
            model.generate()
        run = db.get(SearchRun, data.search_run_id)
        run.status = "completed"
        run.completed_at = utc_now()
        db.commit()
        return {"search_run_id": data.search_run_id, "results": []}

    return executor, qualifier


def _drain_queue(
    *,
    workers: int,
    executor,
    qualifier,
    deadline_s: float,
) -> None:
    """Разбирает очередь `workers` воркерами и гарантированно её опустошает.

    Claim сериализуется явно: PostgreSQL даёт это блокировкой строки
    (`FOR UPDATE SKIP LOCKED`), а SQLite в тестах строчных блокировок не имеет.
    Проверять здесь нужно нагрузку на модель, а не диалект БД.
    """
    claim_lock = threading.Lock()
    original_claim = search_worker.claim_next_job

    def serialized_claim(db, owner=None):
        with claim_lock:
            return original_claim(db, owner)

    search_worker.claim_next_job = serialized_claim
    deadline = monotonic() + deadline_s
    try:

        def worker_loop() -> None:
            idle_rounds = 0
            while monotonic() < deadline:
                if process_next_job(executor=executor, qualifier=qualifier) is None:
                    idle_rounds += 1
                    if idle_rounds >= 3:
                        return
                    sleep(0.02)
                else:
                    idle_rounds = 0

        threads = [
            threading.Thread(target=worker_loop, name=f"load-worker-{index}")
            for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=deadline_s)

        # Задача, возвращённая в очередь в момент выхода воркеров, не должна
        # остаться незамеченной: добираем остаток в основном потоке.
        while monotonic() < deadline:
            if process_next_job(executor=executor, qualifier=qualifier) is None:
                break
    finally:
        search_worker.claim_next_job = original_claim


def _delete_runs(run_ids: list[int]) -> None:
    """Убирает нагрузочные запуски: очередь общая для всего модуля тестов."""
    with SessionLocal() as db:
        for run in db.scalars(
            select(SearchRun).where(SearchRun.id.in_(run_ids))
        ).all():
            db.delete(run)
        db.commit()


def _collect_report(
    run_ids: list[int],
    *,
    workers: int,
    model: LocalModelStub,
    enqueued: int,
    wall_s: float,
    enqueue_wall_s: float,
) -> BurstReport:
    statuses: dict[str, int] = {}
    with SessionLocal() as db:
        for run in db.scalars(
            select(SearchRun).where(SearchRun.id.in_(run_ids))
        ).all():
            statuses[run.status] = statuses.get(run.status, 0) + 1
    return BurstReport(
        enqueued=enqueued,
        workers=workers,
        model_slots=model.slots,
        completed=statuses.get("completed", 0),
        failed=statuses.get("failed", 0),
        unfinished=sum(
            count
            for status, count in statuses.items()
            if status not in {"completed", "failed"}
        ),
        wall_s=wall_s,
        enqueue_wall_s=enqueue_wall_s,
        peak_model_concurrency=model.peak_in_flight,
        model_calls=model.calls,
        model_timeouts=model.timeouts,
        statuses=statuses,
    )


def run_burst(
    client: TestClient,
    headers: dict[str, str],
    *,
    substances: int,
    workers: int,
    model: LocalModelStub,
    offset: int,
    poll_threads: int = 0,
    deadline_s: float = 60.0,
) -> BurstReport:
    """Полный прогон: постановка в очередь, разбор воркерами, сбор итога."""
    responses, enqueue_wall_s = _enqueue_burst(
        client,
        headers,
        count=substances,
        parallelism=min(substances, 16),
        offset=offset,
    )
    assert all(item["status_code"] == 202 for item in responses), [
        item for item in responses if item["status_code"] != 202
    ]
    run_ids = [item["body"]["search_run_id"] for item in responses]

    executor, qualifier = _make_pipeline(model)
    poll_stop = threading.Event()
    poll_stats = {"requests": 0, "errors": 0, "slowest_s": 0.0}
    poll_lock = threading.Lock()

    def poller() -> None:
        while not poll_stop.is_set():
            started = monotonic()
            try:
                status_code = client.get("/search-runs", headers=headers).status_code
            except Exception:  # noqa: BLE001 - отказ сервера тоже результат теста
                status_code = 0
            elapsed = monotonic() - started
            with poll_lock:
                poll_stats["requests"] += 1
                if status_code != 200:
                    poll_stats["errors"] += 1
                poll_stats["slowest_s"] = max(poll_stats["slowest_s"], elapsed)
            sleep(0.05)

    pollers = [
        threading.Thread(target=poller, name=f"load-poller-{index}", daemon=True)
        for index in range(poll_threads)
    ]
    for thread in pollers:
        thread.start()

    started = monotonic()
    try:
        _drain_queue(
            workers=workers,
            executor=executor,
            qualifier=qualifier,
            deadline_s=deadline_s,
        )
    finally:
        wall_s = monotonic() - started
        poll_stop.set()
        for thread in pollers:
            thread.join(timeout=5)

    report = _collect_report(
        run_ids,
        workers=workers,
        model=model,
        enqueued=substances,
        wall_s=wall_s,
        enqueue_wall_s=enqueue_wall_s,
    )
    report.poll_requests = poll_stats["requests"]
    report.poll_errors = poll_stats["errors"]
    report.slowest_poll_s = poll_stats["slowest_s"]
    _delete_runs(run_ids)
    return report


def probe_parallel_search_capacity(
    client: TestClient,
    headers: dict[str, str],
    *,
    model_slots: int,
    worker_ladder: tuple[int, ...],
    substances_per_step: int,
    offset: int,
) -> tuple[int, list[BurstReport]]:
    """Наибольшее число поисков, которое модель ведёт действительно параллельно.

    Ступень засчитывается, если ни один запуск не потерян и все воркеры
    ступени одновременно получили слот модели. Выше этого числа запросы не
    отклоняются — они встают в очередь внутри llama-server, поэтому предел
    виден как выход пиковой параллельности на полку, а не как ошибка.
    """
    reports: list[BurstReport] = []
    capacity = 0
    for step, workers in enumerate(worker_ladder):
        model = LocalModelStub(slots=model_slots, call_seconds=0.03, timeout_s=5.0)
        report = run_burst(
            client,
            headers,
            substances=substances_per_step,
            workers=workers,
            model=model,
            offset=offset + step * 1000,
        )
        reports.append(report)
        if not report.survived or report.peak_model_concurrency < workers:
            break
        capacity = workers
    return capacity, reports


def test_api_accepts_a_parallel_burst_of_substance_searches(client):
    """Постановка в очередь не является узким местом: burst проходит целиком."""
    buyer = _auth(client, "ivanov")
    burst_size = 32
    responses, wall_s = _enqueue_burst(
        client, buyer, count=burst_size, parallelism=16, offset=100_000
    )

    assert [item["status_code"] for item in responses] == [202] * burst_size
    run_ids = {item["body"]["search_run_id"] for item in responses}
    correlation_ids = {item["body"]["correlation_id"] for item in responses}
    assert len(run_ids) == burst_size, "потеряны или склеены задачи поиска"
    assert len(correlation_ids) == burst_size

    positions = sorted(item["body"]["queue_position"] for item in responses)
    assert positions == list(range(1, burst_size + 1)), (
        "позиция в очереди должна оставаться честной под параллельной нагрузкой"
    )
    print(
        f"\nБурст постановки в очередь: {burst_size} веществ за {wall_s:.2f}с"
    )
    _delete_runs(sorted(run_ids))


def test_single_worker_keeps_the_model_at_one_request(client):
    """Очередь растёт, нагрузка на модель — нет: один воркер = один запрос."""
    buyer = _auth(client, "ivanov")
    model = LocalModelStub(slots=4, call_seconds=0.01, timeout_s=2.0)

    report = run_burst(
        client,
        buyer,
        substances=12,
        workers=1,
        model=model,
        offset=200_000,
        poll_threads=4,
    )

    print(f"\n{report.as_line()}")
    assert report.survived, report.statuses
    assert report.peak_model_concurrency == 1, (
        "один воркер не должен занимать несколько слотов модели"
    )
    assert report.model_calls == 12 * LLM_CALLS_PER_RUN
    assert report.poll_errors == 0, "чтение списка задач падало под нагрузкой"
    assert report.poll_requests > 0


def test_workers_beyond_model_slots_produce_outages_not_throughput(client):
    """Краш-тест перегрузки: воркеров больше, чем слотов модели.

    Лишние воркеры не ускоряют поиск — они получают таймаут модели. Задача
    возвращается в очередь ограниченное число раз и затем честно падает,
    но ни один запуск не исчезает.
    """
    buyer = _auth(client, "ivanov")
    model = LocalModelStub(slots=1, call_seconds=0.05, timeout_s=0.02)

    report = run_burst(
        client,
        buyer,
        substances=8,
        workers=4,
        model=model,
        offset=300_000,
    )

    print(f"\n{report.as_line()}")
    assert report.peak_model_concurrency <= model.slots, (
        "заглушка модели не удержала заявленное число слотов"
    )
    assert report.model_timeouts > 0, "перегрузка модели не воспроизвелась"
    assert report.failed > 0, (
        "перегруженная модель должна приводить к явному отказу, а не к тишине"
    )
    assert report.completed + report.failed + report.unfinished == report.enqueued, (
        "ни один запуск не должен потеряться при перегрузке"
    )
    assert not report.survived


def test_capacity_matches_the_number_of_model_slots(client):
    """Реальный предел одновременных поисков — число слотов llama-server."""
    buyer = _auth(client, "ivanov")
    slots = 4
    capacity, reports = probe_parallel_search_capacity(
        client,
        buyer,
        model_slots=slots,
        worker_ladder=(1, 2, 4, 8),
        substances_per_step=8,
        offset=400_000,
    )

    print("\nЛестница нагрузки:")
    for report in reports:
        print(f"  {report.as_line()}")
    print(f"  предел одновременных поисков: {capacity}")

    assert capacity == slots, (
        "пропускная способность должна упираться в слоты модели, "
        f"получено {capacity} при {slots} слотах"
    )
    scaled = next(report for report in reports if report.workers == slots)
    assert scaled.peak_model_concurrency == slots
    assert scaled.survived, scaled.statuses

    oversubscribed = next(
        (report for report in reports if report.workers > slots), None
    )
    assert oversubscribed is not None, "лестница не дошла до перегрузки"
    assert oversubscribed.peak_model_concurrency == slots, (
        "лишние воркеры не получают слот: параллельность выходит на полку"
    )


def test_parallel_workers_shorten_the_wall_time(client):
    """Догоняющая проверка: слоты модели действительно дают ускорение."""
    buyer = _auth(client, "ivanov")
    substances = 8
    serial_model = LocalModelStub(slots=4, call_seconds=0.02, timeout_s=2.0)
    serial = run_burst(
        client,
        buyer,
        substances=substances,
        workers=1,
        model=serial_model,
        offset=500_000,
    )
    parallel_model = LocalModelStub(slots=4, call_seconds=0.02, timeout_s=2.0)
    parallel = run_burst(
        client,
        buyer,
        substances=substances,
        workers=4,
        model=parallel_model,
        offset=510_000,
    )

    print(f"\n{serial.as_line()}\n{parallel.as_line()}")
    assert serial.survived and parallel.survived
    assert parallel.wall_s < serial.wall_s * 0.8, (
        f"параллельные воркеры не дали выигрыша: {parallel.wall_s:.2f}с "
        f"против {serial.wall_s:.2f}с"
    )


def test_queued_claim_is_row_locked_on_postgresql():
    """Масштабирование воркеров безопасно только при блокировке строки.

    Тест фиксирует, что claim на PostgreSQL уходит в БД с
    `FOR UPDATE ... SKIP LOCKED`: без этого два воркера выполнили бы один и
    тот же поиск дважды, потратив вдвое больше GPU и веб-запросов.
    """
    captured: list = []
    # Драйвер psycopg на машине разработчика может отсутствовать, поэтому
    # берём базовый диалект PostgreSQL вместо реального engine.
    pg_dialect = PGDialect()

    class _BindProbe:
        dialect = pg_dialect

    class _SessionProbe:
        bind = _BindProbe()

        def scalar(self, statement):
            captured.append(statement)
            return None

        def commit(self) -> None:
            return None

    assert claim_next_job(_SessionProbe()) is None
    compiled = str(captured[0].compile(dialect=pg_dialect)).upper()
    assert "FOR UPDATE" in compiled
    assert "SKIP LOCKED" in compiled
