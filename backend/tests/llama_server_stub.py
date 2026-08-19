"""Эмулятор llama-server для измерения параллельных запросов без GPU.

Это настоящий HTTP-сервер с OpenAI-совместимым API, поэтому через него
проходит боевой путь кода: `httpx`, таймауты, разбор ошибки переполнения
контекста и логика повторов `LLMClient`. Заглушка воспроизводит три свойства
llama.cpp, из-за которых наивное включение параллельности ломает поиск:

1. `--ctx-size` — общий бюджет KV-кэша, а не бюджет одного запроса. На слот
   приходится `ctx_size // parallel` токенов.
2. Запрос, не помещающийся в контекст слота, отклоняется с HTTP 400 и текстом
   `request (N tokens) exceeds the available context size (M tokens)`.
3. Запросы сверх числа слотов не отклоняются, а ждут освобождения слота.

Скорость генерации задана параметрами, а не измерена: это модель, а не
бенчмарк железа. Настоящие токены в секунду снимает
`backend/scripts/search_load_test.py --mode llm` на самой ВМ. Параметр
`parallel_efficiency` описывает, какую долю выигрыша даёт continuous batching:
1.0 — идеальное масштабирование, 0.0 — полная сериализация. Для нагрузки
ChemSource она заведомо ближе к нижней границе: на вход уходят тексты
страниц (prefill), а не длинная генерация.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, sleep

# Оценка «настоящего токенизатора» сервера. Совпадает с осторожной оценкой
# backend (два символа на токен), поэтому запрос, который backend считает
# помещающимся, действительно помещается при одном слоте.
_CHARS_PER_TOKEN = 2


@dataclass
class ServerStats:
    """Что сервер увидел за прогон."""

    requests: int = 0
    completed: int = 0
    context_rejections: int = 0
    peak_active: int = 0
    total_queue_wait_s: float = 0.0
    total_processing_s: float = 0.0
    prompt_tokens_seen: list[int] = field(default_factory=list)

    @property
    def max_prompt_tokens(self) -> int:
        return max(self.prompt_tokens_seen, default=0)


class LlamaServerStub:
    """Локальный OpenAI-совместимый сервер с ограниченным числом слотов."""

    def __init__(
        self,
        *,
        slots: int = 1,
        ctx_size: int = 12288,
        model: str = "qwen-stub",
        # Скорости сжаты, чтобы набор тестов оставался быстрым; сохранено
        # соотношение, характерное для GPU-инференса: prefill примерно на
        # порядок быстрее декодирования в пересчёте на токен.
        prefill_tokens_per_s: float = 120_000.0,
        decode_tokens_per_s: float = 12_000.0,
        parallel_efficiency: float = 0.4,
    ) -> None:
        self.slots = slots
        self.ctx_size = ctx_size
        self.model = model
        self.prefill_tokens_per_s = prefill_tokens_per_s
        self.decode_tokens_per_s = decode_tokens_per_s
        self.parallel_efficiency = parallel_efficiency
        self.stats = ServerStats()
        self._semaphore = threading.BoundedSemaphore(slots)
        self._lock = threading.Lock()
        self._active = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def ctx_per_slot(self) -> int:
        return self.ctx_size // max(1, self.slots)

    @property
    def base_url(self) -> str:
        assert self._server is not None, "сервер не запущен"
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> "LlamaServerStub":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="llama-stub", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _generate(self, prompt_tokens: int, max_tokens: int) -> None:
        """Занимает слот и «считает» запрос с учётом конкуренции за железо."""
        queue_started = monotonic()
        self._semaphore.acquire()
        processing_started = monotonic()
        try:
            with self._lock:
                self._active += 1
                self.stats.peak_active = max(self.stats.peak_active, self._active)
                self.stats.total_queue_wait_s += monotonic() - queue_started
            nominal_s = (
                prompt_tokens / self.prefill_tokens_per_s
                + max_tokens / self.decode_tokens_per_s
            )
            self._burn(nominal_s)
        finally:
            with self._lock:
                self.stats.total_processing_s += monotonic() - processing_started
                self._active -= 1
            self._semaphore.release()

    def _burn(self, nominal_s: float) -> None:
        """Тратит расчётное время, замедляясь при конкуренции за вычислитель.

        При `parallel_efficiency == 1` соседние запросы не мешают друг другу,
        при 0 — делят одну и ту же пропускную способность.
        """
        done = 0.0
        slice_s = 0.01
        last = monotonic()
        while done < nominal_s:
            sleep(slice_s)
            now = monotonic()
            elapsed, last = now - last, now
            with self._lock:
                active = max(1, self._active)
            # Зачитываем фактически прошедшее время, а не запрошенное: sleep на
            # Windows округляется вверх до кванта таймера, и «идеальная»
            # нарезка раздувала бы длительность запроса в разы.
            slowdown = 1 + (active - 1) * (1 - self.parallel_efficiency)
            done += elapsed / slowdown

    def handle_completion(self, payload: dict) -> tuple[int, dict]:
        """Возвращает (HTTP-код, тело) как настоящий llama-server."""
        prompt_chars = sum(
            len(str(message.get("content") or ""))
            for message in payload.get("messages", [])
        )
        prompt_tokens = -(-prompt_chars // _CHARS_PER_TOKEN)
        max_tokens = int(payload.get("max_tokens") or 512)
        with self._lock:
            self.stats.requests += 1
            self.stats.prompt_tokens_seen.append(prompt_tokens)

        requested = prompt_tokens + max_tokens
        if requested > self.ctx_per_slot:
            with self._lock:
                self.stats.context_rejections += 1
            return 400, {
                "error": {
                    "message": (
                        f"request ({requested} tokens) exceeds the available "
                        f"context size ({self.ctx_per_slot} tokens), "
                        "try increasing it"
                    ),
                    "type": "exceed_context_size_error",
                }
            }

        self._generate(prompt_tokens, max_tokens)
        with self._lock:
            self.stats.completed += 1
        content = json.dumps({"ok": True}, ensure_ascii=False)
        return 200, {
            "id": "chatcmpl-stub",
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            # llama-server отдаёт usage, и нагрузочный скрипт считает по нему
            # токены в секунду вместо оценки по символам.
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": max_tokens,
                "total_tokens": prompt_tokens + max_tokens,
            },
        }

    def slots_snapshot(self) -> list[dict]:
        """Ответ /slots: занятость слотов, как у llama-server с --metrics."""
        with self._lock:
            active = self._active
        return [
            {
                "id": index,
                "n_ctx": self.ctx_per_slot,
                "state": 1 if index < active else 0,
            }
            for index in range(self.slots)
        ]

    def metrics_text(self) -> str:
        """Ответ /metrics в формате Prometheus, подмножество llama-server."""
        with self._lock:
            active = self._active
            requests = self.stats.requests
        return (
            f"llamacpp:requests_processing {active}\n"
            f"llamacpp:requests_deferred 0\n"
            f"llamacpp:n_decode_total {requests}\n"
        )


def _make_handler(stub: LlamaServerStub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: object) -> None:  # тишина в выводе тестов
            return

        def _respond(self, status: int, body: object) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _respond_text(self, status: int, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - имя задано BaseHTTPRequestHandler
            if self.path.endswith("/models"):
                self._respond(
                    200, {"object": "list", "data": [{"id": stub.model}]}
                )
            elif self.path.endswith("/slots"):
                # llama-server отдаёт массив слотов, а не объект.
                self._respond(200, stub.slots_snapshot())
            elif self.path.endswith("/metrics"):
                self._respond_text(200, stub.metrics_text())
            else:
                self._respond(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802 - имя задано BaseHTTPRequestHandler
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not self.path.endswith("/chat/completions"):
                self._respond(404, {"error": {"message": "not found"}})
                return
            status, body = stub.handle_completion(payload)
            self._respond(status, body)

    return Handler
