"""Эксперименты по параллельной работе локальной LLM: замер и сравнение.

В отличие от `test_search_load.py`, где модель заменена Python-объектом, здесь
воркер ходит в настоящий HTTP-сервер :class:`LlamaServerStub` через боевой
`LLMClient`. Проверяется то, что ломается при включении параллельности:
деление контекста между слотами, ожидание слота, разбор ошибки переполнения и
поведение очереди при отказе модели.

Что здесь измеряется, а что нет:

- измеряется поведение кода и режимы отказа при разных конфигурациях
  `--parallel` / `--ctx-size`;
- НЕ измеряется скорость железа. Токены в секунду задаются параметрами
  заглушки; настоящие цифры снимает
  `scripts/search_load_test.py --mode llm` на ВМ.

Запуск с таблицами сравнения::

    pytest tests/test_parallel_llm.py -q -s
"""

import json
import os
import threading
from dataclasses import dataclass
from time import monotonic

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_parallel_llm.db")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import search_worker
from app.api.supplier_search import _page_text_budget
from app.services import llm_capacity
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.extraction.llm_client import (
    LLMClient,
    LLMContextOverflowError,
    LLMUnavailableError,
)
from app.main import app
from app.models import SearchRun
from app.search_worker import (
    process_next_job,
    recover_interrupted_jobs,
    requeue_after_llm_outage,
)
from app.services.search_lease import grant_lease
from app.services.search_trace import utc_now
from tests.llama_server_stub import LlamaServerStub

# Профиль обращений одного запуска к модели: идентификация и планировщик на
# этапе поиска, затем квалификация и проверка пакетами по два источника.
SEARCH_PHASE_CALLS = 2
QUALIFICATION_PHASE_CALLS = 6

# Ответ модели на служебных этапах ограничен явно, см. _QUALIFICATION_SCHEMA.
QUALIFICATION_MAX_TOKENS = 1536
PLANNER_MAX_TOKENS = 768

_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_parallel_llm.db"):
        os.remove("test_parallel_llm.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if os.path.exists("test_parallel_llm.db"):
        os.remove("test_parallel_llm.db")


def _auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _synthetic_cas(index: int) -> str:
    body = f"{index:09d}"
    check = sum(int(d) * i for i, d in enumerate(reversed(body), start=1)) % 10
    return f"{body[:7]}-{body[7:]}-{check}"


def _qualification_user_text() -> str:
    """Полезная нагрузка размером с настоящий вызов квалификации.

    Объём страниц backend считает сам под `LLM_CONTEXT_TOKENS`, поэтому берём
    ту же функцию, что и конвейер: запрос будет ровно такой, какой уходит в
    модель на этапе квалификации.
    """
    page_chars = _page_text_budget()
    sources = [
        {
            "result_index": index,
            "domain": f"supplier-{index}.example",
            "text": "x" * page_chars,
        }
        for index in range(2)
    ]
    return json.dumps(
        {"chemical": {"cas": "50-78-2", "name": "Aspirin"}, "sources": sources},
        ensure_ascii=False,
    )


@dataclass
class Experiment:
    """Одна конфигурация и её результат."""

    name: str
    slots: int
    ctx_size: int
    workers: int
    substances: int
    completed: int = 0
    failed: int = 0
    unfinished: int = 0
    wall_s: float = 0.0
    peak_active: int = 0
    llm_requests: int = 0
    context_rejections: int = 0
    ctx_per_slot: int = 0
    max_prompt_tokens: int = 0

    def as_row(self) -> str:
        return (
            f"{self.name:<28} слотов={self.slots} "
            f"ctx={self.ctx_size:>5} на слот={self.ctx_per_slot:>5} "
            f"воркеров={self.workers} | завершено={self.completed}/"
            f"{self.substances} провалено={self.failed} "
            f"пик={self.peak_active} отказов по контексту="
            f"{self.context_rejections:>3} время={self.wall_s:6.2f}с"
        )


def _enqueue(client: TestClient, headers: dict[str, str], *, count: int, offset: int):
    run_ids = []
    for index in range(count):
        response = client.post(
            "/supplier-search/jobs",
            headers=headers,
            json={
                "cas": _synthetic_cas(offset + index),
                "name": f"Parallel probe {offset + index}",
                "country": "Китай",
            },
        )
        assert response.status_code == 202, response.text
        run_ids.append(response.json()["search_run_id"])
    return run_ids


def _make_pipeline(base_url: str):
    """Этапы конвейера, обращающиеся к модели по настоящему HTTP."""
    llm = LLMClient(base_url=base_url, timeout_s=30)
    user_text = _qualification_user_text()

    def call(max_tokens: int) -> None:
        llm.generate_json(
            system_prompt="Оцени источники и верни строгий JSON.",
            user_text=user_text,
            schema_name="probe",
            json_schema=_SCHEMA,
            max_tokens=max_tokens,
        )

    def executor(data, db, user, *, search_run):
        for _ in range(SEARCH_PHASE_CALLS):
            call(PLANNER_MAX_TOKENS)
        return {
            "search_run_id": search_run.id,
            "query": f'"{data.cas}" manufacturer',
            "queries_used": [f'"{data.cas}" manufacturer'],
            "results": [
                {
                    "title": data.name,
                    "url": f"https://supplier-{search_run.id}.example/product",
                    "snippet": data.name,
                    "country_hint": "likely",
                }
            ],
        }

    def qualifier(data, db, user):
        for _ in range(QUALIFICATION_PHASE_CALLS):
            call(QUALIFICATION_MAX_TOKENS)
        run = db.get(SearchRun, data.search_run_id)
        run.status = "completed"
        run.completed_at = utc_now()
        db.commit()
        return {"search_run_id": data.search_run_id, "results": []}

    return executor, qualifier


def _drain(*, workers: int, executor, qualifier, deadline_s: float) -> None:
    """Разбирает очередь. Claim сериализован так же, как строчная блокировка."""
    claim_lock = threading.Lock()
    original_claim = search_worker.claim_next_job

    def serialized_claim(db, owner=None):
        with claim_lock:
            return original_claim(db, owner)

    search_worker.claim_next_job = serialized_claim
    deadline = monotonic() + deadline_s
    try:

        def loop() -> None:
            idle = 0
            while monotonic() < deadline:
                if process_next_job(executor=executor, qualifier=qualifier) is None:
                    idle += 1
                    if idle >= 3:
                        return
                else:
                    idle = 0

        threads = [
            threading.Thread(target=loop, name=f"probe-worker-{index}")
            for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=deadline_s)
        while monotonic() < deadline:
            if process_next_job(executor=executor, qualifier=qualifier) is None:
                break
    finally:
        search_worker.claim_next_job = original_claim


def run_experiment(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    slots: int,
    ctx_size: int,
    workers: int,
    substances: int,
    offset: int,
    parallel_efficiency: float = 0.4,
) -> Experiment:
    run_ids = _enqueue(client, headers, count=substances, offset=offset)
    experiment = Experiment(
        name=name,
        slots=slots,
        ctx_size=ctx_size,
        workers=workers,
        substances=substances,
    )
    # Ограничитель обращений к модели должен знать конфигурацию сервера:
    # он выдаёт ровно столько мест, сколько у llama-server слотов. Иначе
    # эксперимент с четырьмя слотами упрётся в значение по умолчанию.
    previous_slots = os.environ.get("LLM_PARALLEL_SLOTS")
    os.environ["LLM_PARALLEL_SLOTS"] = str(slots)
    llm_capacity._fallback_lock = threading.BoundedSemaphore(slots)
    with LlamaServerStub(
        slots=slots,
        ctx_size=ctx_size,
        parallel_efficiency=parallel_efficiency,
    ) as stub:
        experiment.ctx_per_slot = stub.ctx_per_slot
        executor, qualifier = _make_pipeline(stub.base_url)
        started = monotonic()
        _drain(
            workers=workers,
            executor=executor,
            qualifier=qualifier,
            deadline_s=120.0,
        )
        experiment.wall_s = monotonic() - started
        experiment.peak_active = stub.stats.peak_active
        experiment.llm_requests = stub.stats.requests
        experiment.context_rejections = stub.stats.context_rejections
        experiment.max_prompt_tokens = stub.stats.max_prompt_tokens

    if previous_slots is None:
        os.environ.pop("LLM_PARALLEL_SLOTS", None)
    else:
        os.environ["LLM_PARALLEL_SLOTS"] = previous_slots

    with SessionLocal() as db:
        runs = db.scalars(select(SearchRun).where(SearchRun.id.in_(run_ids))).all()
        for run in runs:
            if run.status == "completed":
                experiment.completed += 1
            elif run.status == "failed":
                experiment.failed += 1
            else:
                experiment.unfinished += 1
        for run in runs:
            db.delete(run)
        db.commit()
    return experiment


# --- Шаг 1. Базовая линия: как сейчас в проде -------------------------------


def test_step1_baseline_single_slot(client):
    """Текущая конфигурация ВМ: --parallel 1 --ctx-size 12288, один воркер."""
    buyer = _auth(client, "ivanov")
    settings = get_settings()
    experiment = run_experiment(
        client,
        buyer,
        name="1. база: 1 слот, 1 воркер",
        slots=1,
        ctx_size=settings.llm_context_tokens,
        workers=1,
        substances=4,
        offset=600_000,
    )
    print(f"\n{experiment.as_row()}")
    print(
        f"   запрос к модели: {experiment.max_prompt_tokens} токенов промпта "
        f"при контексте слота {experiment.ctx_per_slot}"
    )

    assert experiment.completed == experiment.substances, "базовая линия не прошла"
    assert experiment.context_rejections == 0
    assert experiment.peak_active == 1
    assert experiment.llm_requests == experiment.substances * (
        SEARCH_PHASE_CALLS + QUALIFICATION_PHASE_CALLS
    )


# --- Шаг 2. Наивное включение параллельности --------------------------------


def test_step2_naive_parallel_breaks_on_context(client):
    """--parallel 4 без роста --ctx-size: контекст слота падает вчетверо.

    Это главный практический риск: конфигурация выглядит рабочей, сервер
    поднимается, health-check зелёный, а каждый содержательный вызов
    отклоняется по контексту.
    """
    buyer = _auth(client, "ivanov")
    settings = get_settings()
    experiment = run_experiment(
        client,
        buyer,
        name="2. наивно: 4 слота, тот же ctx",
        slots=4,
        ctx_size=settings.llm_context_tokens,
        workers=4,
        substances=4,
        offset=610_000,
    )
    print(f"\n{experiment.as_row()}")
    print(
        f"   контекст слота упал до {experiment.ctx_per_slot} токенов, "
        f"а запрос требует {experiment.max_prompt_tokens} + max_tokens"
    )

    assert experiment.context_rejections > 0, "переполнение контекста не воспроизвелось"
    assert experiment.completed == 0, "запуски не должны завершаться успешно"
    assert experiment.failed == experiment.substances, "отказ должен быть явным"
    # Ошибка конфигурации детерминирована: одна попытка на запуск, без
    # повторов. До правки воркер возвращал такую задачу в очередь трижды и
    # делал по четыре обращения к модели вместо одного.
    assert experiment.context_rejections == experiment.substances, (
        "переполнение контекста не должно повторяться: "
        f"{experiment.context_rejections} обращений на "
        f"{experiment.substances} запусков"
    )


def test_step2_context_overflow_is_reported_as_real_reason(client):
    """Переполнение контекста доходит до пользователя настоящей причиной."""
    settings = get_settings()
    with LlamaServerStub(slots=4, ctx_size=settings.llm_context_tokens) as stub:
        llm = LLMClient(base_url=stub.base_url, timeout_s=30)
        with pytest.raises(LLMUnavailableError) as excinfo:
            llm.generate_json(
                system_prompt="Оцени источники.",
                user_text=_qualification_user_text(),
                schema_name="probe",
                json_schema=_SCHEMA,
                max_tokens=QUALIFICATION_MAX_TOKENS,
            )
    message = str(excinfo.value)
    print(f"\nСообщение пользователю: {message}")
    assert "контекст" in message
    # Повтор не выполняется: он не изменит результат и занял бы слот GPU.
    assert len(llm.last_attempts) == 1


def test_step2_context_overflow_is_not_requeued(client):
    """Переполнение контекста падает сразу, а временный сбой — повторяется.

    Проверяются оба пути доставки ошибки: прямое исключение и HTTPException
    503, в которую этап квалификации заворачивает причину.
    """
    buyer = _auth(client, "ivanov")
    run_ids = _enqueue(client, buyer, count=1, offset=670_000)
    overflow = LLMContextOverflowError(
        "Запрос к модели (5772 токенов) не помещается в её контекст (3072)"
    )
    wrapped = HTTPException(
        status_code=503, detail={"message": "переполнение", "search_run_id": 1}
    )
    wrapped.__cause__ = overflow

    with SessionLocal() as db:
        run = db.get(SearchRun, run_ids[0])
        assert requeue_after_llm_outage(db, run, overflow) is False
        assert requeue_after_llm_outage(db, run, wrapped) is False
        # Настоящая недоступность модели по-прежнему возвращается в очередь.
        assert requeue_after_llm_outage(
            db, run, LLMUnavailableError("connection refused")
        ) is True
        assert run.status == "queued"
        db.delete(run)
        db.commit()


# --- Шаг 3. Корректная параллельность ---------------------------------------


def test_step3_parallel_with_scaled_context(client):
    """--parallel 4 --ctx-size 4x: у каждого запроса свой полный контекст."""
    buyer = _auth(client, "ivanov")
    settings = get_settings()
    slots = 4
    experiment = run_experiment(
        client,
        buyer,
        name="3. верно: 4 слота, ctx x4",
        slots=slots,
        ctx_size=settings.llm_context_tokens * slots,
        workers=slots,
        substances=4,
        offset=620_000,
    )
    print(f"\n{experiment.as_row()}")

    assert experiment.completed == experiment.substances, "корректная конфигурация не прошла"
    assert experiment.context_rejections == 0
    assert experiment.ctx_per_slot == settings.llm_context_tokens
    assert experiment.peak_active == slots, "слоты не были заняты одновременно"


# --- Шаг 4. Сравнение конфигураций ------------------------------------------


def test_step4_compare_configurations(client):
    """Сводная таблица: что даёт параллельность и от чего это зависит."""
    buyer = _auth(client, "ivanov")
    settings = get_settings()
    ctx = settings.llm_context_tokens
    substances = 4
    experiments = [
        run_experiment(
            client,
            buyer,
            name="1 слот / 1 воркер",
            slots=1,
            ctx_size=ctx,
            workers=1,
            substances=substances,
            offset=630_000,
        ),
        run_experiment(
            client,
            buyer,
            name="4 слота / 4 воркера (eff 0.4)",
            slots=4,
            ctx_size=ctx * 4,
            workers=4,
            substances=substances,
            offset=640_000,
            parallel_efficiency=0.4,
        ),
        run_experiment(
            client,
            buyer,
            name="4 слота / 4 воркера (eff 0.9)",
            slots=4,
            ctx_size=ctx * 4,
            workers=4,
            substances=substances,
            offset=650_000,
            parallel_efficiency=0.9,
        ),
    ]

    print("\nСравнение конфигураций:")
    baseline = experiments[0]
    for experiment in experiments:
        speedup = baseline.wall_s / experiment.wall_s if experiment.wall_s else 0
        print(f"  {experiment.as_row()} ускорение={speedup:.2f}x")
    print(
        "  eff — доля выигрыша от continuous batching; для prefill-heavy "
        "нагрузки ChemSource она ближе к 0.4, чем к 0.9. Настоящее значение "
        "снимается на ВМ через scripts/search_load_test.py --mode llm."
    )

    for experiment in experiments:
        assert experiment.completed == substances, experiment.name
    prefill_heavy, decode_friendly = experiments[1], experiments[2]
    assert decode_friendly.wall_s < baseline.wall_s, (
        "при высокой эффективности батчинга параллельность обязана выигрывать"
    )
    assert prefill_heavy.wall_s > decode_friendly.wall_s * 1.3, (
        "выигрыш от слотов сильно зависит от эффективности батчинга; "
        "нельзя обещать кратное ускорение, не измерив её на железе"
    )


# --- Шаг 5. Блокер: восстановление задач при рестарте ------------------------


def test_step5_restart_spares_jobs_running_on_other_workers(client):
    """Замер эффекта аренды на том же сценарии, что выявил блокер.

    До аренды `recover_interrupted_jobs` помечал `failed` все незавершённые
    задачи очереди и рестарт одного воркера обрывал чужой идущий поиск.
    Теперь живая чужая аренда неприкосновенна, а брошенная задача
    по-прежнему восстанавливается.
    """
    buyer = _auth(client, "ivanov")
    run_ids = _enqueue(client, buyer, count=2, offset=660_000)
    in_flight, abandoned = run_ids

    with SessionLocal() as db:
        running = db.get(SearchRun, in_flight)
        running.status = "fetching_sources"
        grant_lease(running, "worker-remote")  # выполняется другим воркером
        stalled = db.get(SearchRun, abandoned)
        stalled.status = "identifying"  # воркер упал, аренды нет
        db.commit()

    with SessionLocal() as db:
        recovered = recover_interrupted_jobs(db, "worker-local")

    with SessionLocal() as db:
        running = db.get(SearchRun, in_flight)
        stalled = db.get(SearchRun, abandoned)
        running_status, stalled_status = running.status, stalled.status
        db.delete(running)
        db.delete(stalled)
        db.commit()

    print(
        f"\nРестарт воркера: восстановлено {recovered} из 2; "
        f"чужая выполняемая задача — '{running_status}', "
        f"брошенная — '{stalled_status}'"
    )
    assert recovered == 1, "до аренды рестарт обрывал обе задачи"
    assert running_status == "fetching_sources"
    assert stalled_status == "failed"
