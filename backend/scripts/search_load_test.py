"""Краш-тест реального контура: сколько веществ ищется одновременно.

Автономный тест `backend/tests/test_search_load.py` измеряет поведение
конвейера на заглушке модели. Этот скрипт измеряет само железо: сколько
одновременных генераций держит локальная llama-server и сколько запусков
поиска переживает развёрнутый стенд.

Скрипт обращается к внешним системам, поэтому запускается только вручную и
только с флагом ``--yes``.

Режимы
------

``--mode llm`` — безопасный старт. Шлёт короткие запросы прямо в
``LLM_BASE_URL`` лестницей параллельности и показывает, где растёт задержка и
начинаются ошибки. Ничего не пишет в базу и не ходит в веб.

``--mode queue`` — полный сквозной прогон: ставит N веществ в очередь через
API стенда и ждёт терминального статуса каждой задачи. Настоящий поиск ходит
в веб-выдачу и на сайты поставщиков, поэтому берите тестовый стенд, а не
рабочий контур заказчика.

Примеры (из каталога backend/)::

    python scripts/search_load_test.py --mode llm --yes \\
        --ladder 1,2,4,8 --requests-per-step 8

    python scripts/search_load_test.py --mode queue --yes \\
        --base-url http://localhost:8000 --username ivanov --password demo123 \\
        --substances 6 --timeout-s 5400

Код возврата: 0 — все ступени прошли; 1 — на какой-то ступени были отказы.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

# Реальные вещества с корректной контрольной цифрой CAS: поиск обязан
# принимать их без правок и находить настоящих производителей.
DEFAULT_SUBSTANCES: list[tuple[str, str]] = [
    ("50-78-2", "Acetylsalicylic acid"),
    ("64-17-5", "Ethanol"),
    ("57-13-6", "Urea"),
    ("50-81-7", "Ascorbic acid"),
    ("110-15-6", "Succinic acid"),
    ("77-92-9", "Citric acid"),
    ("87-69-4", "L-tartaric acid"),
    ("56-40-6", "Glycine"),
    ("107-43-7", "Betaine"),
    ("59-51-8", "DL-methionine"),
    ("60-33-3", "Linoleic acid"),
    ("69-65-8", "Mannitol"),
]

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class StepResult:
    """Одна ступень лестницы параллельности."""

    concurrency: int
    profile: str = "-"
    successes: int = 0
    failures: int = 0
    wall_s: float = 0.0
    latencies_s: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    peak_slots_busy: int = 0
    server_state: dict = field(default_factory=dict)

    @property
    def mean_prompt_tokens(self) -> float:
        """Фактическая длина промпта по usage: оценка по символам неточна."""
        return self.prompt_tokens / self.successes if self.successes else 0.0

    @property
    def prompt_tokens_per_s(self) -> float:
        return self.prompt_tokens / self.wall_s if self.wall_s else 0.0

    @property
    def completion_tokens_per_s(self) -> float:
        return self.completion_tokens / self.wall_s if self.wall_s else 0.0

    @property
    def requests_per_hour(self) -> float:
        return self.throughput_per_min * 60

    @property
    def survived(self) -> bool:
        return self.failures == 0

    @property
    def p50_s(self) -> float:
        return statistics.median(self.latencies_s) if self.latencies_s else 0.0

    @property
    def p95_s(self) -> float:
        if not self.latencies_s:
            return 0.0
        ordered = sorted(self.latencies_s)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[index]

    @property
    def throughput_per_min(self) -> float:
        if self.wall_s <= 0:
            return 0.0
        return self.successes / self.wall_s * 60

    def as_line(self) -> str:
        return (
            f"профиль={self.profile:<7} параллельность={self.concurrency:>3} "
            f"успешно={self.successes:>3} отказов={self.failures:>3} "
            f"p50={self.p50_s:7.2f}с p95={self.p95_s:7.2f}с "
            f"промпт={self.mean_prompt_tokens:6.0f} т "
            f"prefill={self.prompt_tokens_per_s:8.1f} т/с "
            f"генерация={self.completion_tokens_per_s:7.1f} т/с "
            f"запросов/час={self.requests_per_hour:8.1f} "
            f"пик слотов={self.peak_slots_busy}"
        )


_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_type": {"type": "string"},
        "cas_status": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["supplier_type", "cas_status", "evidence_quote"],
    "additionalProperties": False,
}


# Обрывки текста, похожие на первичные страницы поставщиков: смесь языков,
# цифр и单位, как в каталогах и спецификациях. Повторяющийся одиночный символ
# для этой цели не годится — он токенизируется в разы плотнее реального
# текста, и замер занижал бы длину промпта в несколько раз.
_FILLER_WORDS = (
    "Shandong Hongyuan Chemical Co Ltd manufacturer CAS 50-78-2 purity 99.5% "
    "USP grade acetylsalicylic acid white crystalline powder 25 kg drum FOB "
    "Qingdao MOQ 500 kg GMP ISO9001 certificate of analysis available upon "
    "request 生产厂家 阿司匹林 原料药 出口 欧盟 认证 品质保证 поставка со склада "
    "в Москве сертификат анализа паспорт качества таможенное оформление "
    "molecular formula C9H8O4 molecular weight 180.16 storage in a cool dry "
    "place away from direct sunlight shelf life 36 months packaging 25kg "
)
# Средняя длина токена в такой смеси заметно меньше двух символов, поэтому
# точная длина промпта не вычисляется, а берётся из usage ответа модели.
_CHARS_PER_TOKEN_ESTIMATE = 3.2


def _filler_text(target_tokens: int) -> str:
    """Текст примерно на target_tokens токенов, похожий на реальные страницы."""
    needed_chars = int(target_tokens * _CHARS_PER_TOKEN_ESTIMATE)
    repeats = needed_chars // len(_FILLER_WORDS) + 1
    return (_FILLER_WORDS * repeats)[:needed_chars]


@dataclass(frozen=True)
class Profile:
    """Форма запроса к модели.

    Короткий профиль почти ничего не говорит о настоящем поиске: нагрузка
    ChemSource prefill-heavy — на вход уходят тексты первичных страниц, на
    выход короткий structured output. Мерить надо тем, что реально пойдёт в
    модель, иначе конфигурация с делённым контекстом пройдёт проверку и
    развалится в бою.
    """

    name: str
    prompt_tokens: int
    max_tokens: int
    structured: bool

    def payload(self, model: str) -> dict:
        filler = _filler_text(self.prompt_tokens)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Оцени источники и верни строгий JSON.",
                },
                {"role": "user", "content": filler},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self.structured:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "supplier_qualification",
                    "schema": _PROBE_SCHEMA,
                    "strict": True,
                },
            }
        return payload


PROFILES = {
    # Служебные вызовы: планировщик запросов, короткие проверки.
    "short": Profile("short", prompt_tokens=400, max_tokens=256, structured=False),
    # Этап квалификации: два текста первичных страниц в одном запросе.
    "search": Profile("search", prompt_tokens=9000, max_tokens=1536, structured=True),
}


def _probe_llm_once(
    client: httpx.Client, url: str, model: str, profile: Profile
) -> tuple[float, int, int]:
    """Один запрос к модели. Возвращает (задержка, prompt_tokens, completion)."""
    started = time.monotonic()
    response = client.post(url, json=profile.payload(model))
    response.raise_for_status()
    body = response.json()
    body["choices"][0]["message"]["content"]
    usage = body.get("usage") or {}
    return (
        time.monotonic() - started,
        int(usage.get("prompt_tokens") or profile.prompt_tokens),
        int(usage.get("completion_tokens") or 0),
    )


def read_server_state(base_url: str, api_key: str) -> dict:
    """Снимает /slots и /metrics llama-server (нужен запуск с --metrics).

    Отсутствие эндпоинтов не является ошибкой прогона: сервер мог быть
    запущен без --metrics, тогда просто нечего показать.
    """
    root = base_url.rstrip("/").removesuffix("/v1")
    headers = {"Authorization": f"Bearer {api_key}"}
    state: dict = {}
    try:
        response = httpx.get(f"{root}/slots", headers=headers, timeout=5.0)
        if response.status_code == 200:
            slots = response.json()
            if isinstance(slots, list):
                state["slots_total"] = len(slots)
                state["slots_busy"] = sum(
                    1 for slot in slots if slot.get("state")
                )
    except Exception:  # noqa: BLE001 - диагностика не должна ронять прогон
        pass
    try:
        response = httpx.get(f"{root}/metrics", headers=headers, timeout=5.0)
        if response.status_code == 200:
            for line in response.text.splitlines():
                if line.startswith("llamacpp:requests_"):
                    key, _, value = line.partition(" ")
                    state[key.replace("llamacpp:", "")] = value.strip()
    except Exception:  # noqa: BLE001
        pass
    return state


def run_llm_step(
    *,
    concurrency: int,
    requests_count: int,
    base_url: str,
    model: str,
    api_key: str,
    timeout_s: float,
    profile: Profile,
) -> StepResult:
    """Лестница параллельности прямо по эндпоинту модели."""
    step = StepResult(concurrency=concurrency, profile=profile.name)
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    peak_busy = 0
    watching = threading.Event()

    def watch_slots() -> None:
        nonlocal peak_busy
        while not watching.is_set():
            state = read_server_state(base_url, api_key)
            busy = state.get("slots_busy")
            if isinstance(busy, int):
                peak_busy = max(peak_busy, busy)
            time.sleep(0.05)

    def one(_: int) -> tuple[float | None, int, int, str | None]:
        try:
            with httpx.Client(timeout=timeout_s, headers=headers) as client:
                latency, prompt_tokens, completion = _probe_llm_once(
                    client, url, model, profile
                )
                return latency, prompt_tokens, completion, None
        except Exception as exc:  # noqa: BLE001 - отказ модели и есть результат
            return None, 0, 0, f"{type(exc).__name__}: {exc}"[:200]

    watcher = threading.Thread(target=watch_slots, daemon=True)
    watcher.start()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for latency, prompt_tokens, completion, error in pool.map(
            one, range(requests_count)
        ):
            if error is None and latency is not None:
                step.successes += 1
                step.latencies_s.append(latency)
                step.prompt_tokens += prompt_tokens
                step.completion_tokens += completion
            else:
                step.failures += 1
                if error is not None and error not in step.errors:
                    step.errors.append(error)
    step.wall_s = time.monotonic() - started
    watching.set()
    watcher.join(timeout=2)
    step.peak_slots_busy = peak_busy
    step.server_state = read_server_state(base_url, api_key)
    return step


def login(base_url: str, username: str, password: str, timeout_s: float) -> str:
    response = httpx.post(
        f"{base_url.rstrip('/')}/auth/login",
        json={"username": username, "password": password},
        timeout=timeout_s,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def run_queue_step(
    *,
    base_url: str,
    token: str,
    substances: list[tuple[str, str]],
    country: str,
    timeout_s: float,
    poll_interval_s: float,
) -> StepResult:
    """Ставит вещества в очередь одновременно и ждёт терминальных статусов."""
    step = StepResult(concurrency=len(substances))
    headers = {"Authorization": f"Bearer {token}"}
    api = base_url.rstrip("/")
    started = time.monotonic()

    def enqueue(item: tuple[str, str]) -> tuple[int | None, str | None]:
        cas, name = item
        try:
            response = httpx.post(
                f"{api}/supplier-search/jobs",
                headers=headers,
                json={"cas": cas, "name": name, "country": country},
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json()["search_run_id"], None
        except Exception as exc:  # noqa: BLE001
            return None, f"{name}: {type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(max_workers=len(substances)) as pool:
        enqueued = list(pool.map(enqueue, substances))

    pending: dict[int, float] = {}
    for run_id, error in enqueued:
        if run_id is None:
            step.failures += 1
            if error is not None:
                step.errors.append(error)
        else:
            pending[run_id] = started

    print(f"  поставлено в очередь: {len(pending)} из {len(substances)}")
    deadline = started + timeout_s
    while pending and time.monotonic() < deadline:
        time.sleep(poll_interval_s)
        try:
            response = httpx.get(f"{api}/search-runs", headers=headers, timeout=60.0)
            response.raise_for_status()
            listed = {item["id"]: item for item in response.json()}
        except Exception as exc:  # noqa: BLE001
            print(f"  список задач недоступен: {type(exc).__name__}: {exc}")
            continue
        for run_id in list(pending):
            item = listed.get(run_id)
            if item is None or item["status"] not in TERMINAL_STATUSES:
                continue
            latency = time.monotonic() - pending.pop(run_id)
            if item["status"] == "completed":
                step.successes += 1
                step.latencies_s.append(latency)
            else:
                step.failures += 1
                step.errors.append(f"run {run_id}: {item['status']}")
            print(
                f"  задача {run_id}: {item['status']} за {latency / 60:.1f} мин"
            )

    for run_id in pending:
        step.failures += 1
        step.errors.append(f"run {run_id}: не завершилась за {timeout_s:.0f}с")
    step.wall_s = time.monotonic() - started
    return step


def _parse_ladder(value: str) -> list[int]:
    ladder = [int(item) for item in value.split(",") if item.strip()]
    if not ladder or any(item < 1 for item in ladder):
        raise argparse.ArgumentTypeError("ступени лестницы — целые числа от 1")
    return ladder


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=("llm", "queue"), default="llm")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="подтверждает обращение к реальной модели и стенду",
    )
    parser.add_argument("--ladder", type=_parse_ladder, default="1,2,4,8")
    parser.add_argument("--requests-per-step", type=int, default=8)
    parser.add_argument(
        "--profile",
        choices=(*PROFILES, "both"),
        default="search",
        help="short — служебный вызов; search — реальный запрос квалификации",
    )
    parser.add_argument("--llm-base-url", default=settings.llm_base_url)
    parser.add_argument("--llm-model", default=settings.llm_model)
    parser.add_argument("--llm-api-key", default=settings.llm_api_key)
    parser.add_argument("--llm-timeout-s", type=float, default=120.0)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="ivanov")
    parser.add_argument("--password", default="demo123")
    parser.add_argument("--substances", type=int, default=4)
    parser.add_argument("--country", default="Китай")
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=5400.0,
        help="сколько ждать терминального статуса всех задач очереди",
    )
    parser.add_argument("--poll-interval-s", type=float, default=10.0)
    args = parser.parse_args()

    if not args.yes:
        parser.error(
            "нагрузочный прогон обращается к реальной модели и стенду; "
            "подтвердите флагом --yes"
        )
    ladder = (
        args.ladder if isinstance(args.ladder, list) else _parse_ladder(args.ladder)
    )

    steps: list[StepResult] = []
    if args.mode == "llm":
        print(
            f"Лестница параллельности по модели {args.llm_model} "
            f"на {args.llm_base_url}"
        )
        profiles = (
            list(PROFILES.values())
            if args.profile == "both"
            else [PROFILES[args.profile]]
        )
        for profile in profiles:
            print(
                f"\nПрофиль {profile.name}: вход ~{profile.prompt_tokens} "
                f"токенов, выход до {profile.max_tokens}"
            )
            for concurrency in ladder:
                step = run_llm_step(
                    concurrency=concurrency,
                    requests_count=max(args.requests_per_step, concurrency),
                    base_url=args.llm_base_url,
                    model=args.llm_model,
                    api_key=args.llm_api_key,
                    timeout_s=args.llm_timeout_s,
                    profile=profile,
                )
                steps.append(step)
                print(f"  {step.as_line()}")
                for error in step.errors[:3]:
                    print(f"  отказ: {error}")
                if not step.survived:
                    print("  ступень не пройдена, подъём остановлен")
                    break
    else:
        if args.substances > len(DEFAULT_SUBSTANCES):
            parser.error(
                f"в наборе {len(DEFAULT_SUBSTANCES)} веществ; "
                "добавьте свои в DEFAULT_SUBSTANCES"
            )
        print(
            f"Сквозной прогон: {args.substances} веществ одновременно "
            f"через {args.base_url}"
        )
        print(
            "ВНИМАНИЕ: реальный поиск ходит в веб-выдачу и на сайты "
            "поставщиков. Используйте тестовый стенд."
        )
        token = login(args.base_url, args.username, args.password, 30.0)
        step = run_queue_step(
            base_url=args.base_url,
            token=token,
            substances=DEFAULT_SUBSTANCES[: args.substances],
            country=args.country,
            timeout_s=args.timeout_s,
            poll_interval_s=args.poll_interval_s,
        )
        steps.append(step)
        print(f"\n{step.as_line()}")
        for error in step.errors[:5]:
            print(f"  отказ: {error}")

    survived = [step for step in steps if step.survived]
    print("\nИтог:")
    for step in steps:
        print(f"  {step.as_line()}")
    if survived:
        best = max(survived, key=lambda step: step.concurrency)
        print(
            f"  наибольшая параллельность без отказов: {best.concurrency} "
            f"(p95 {best.p95_s:.2f}с)"
        )
    else:
        print("  ни одна ступень не прошла без отказов")
    return 0 if steps and all(step.survived for step in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
