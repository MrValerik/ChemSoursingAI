"""Места у модели выдаются явно и по числу слотов.

Замер на стенде: три параллельных поиска на двух слотах llama-server.
Третий вызов встал в очередь сервера, превысил таймаут запроса, тайм-аут
поднялся как «модель недоступна», этап перезапустился и снова стал
третьим. Семь отказов подряд, сорок четыре минуты без результата — при
живой модели, которая всё это время обрабатывала запросы.
"""

import os
import threading
from time import sleep

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_llm_capacity.db")

import pytest

from app.services import llm_capacity
from app.services.llm_capacity import configured_slots, model_slot


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    """Без базы модуль обязан ограничивать параллельность в памяти."""
    monkeypatch.setattr(llm_capacity, "_session_factory", None)
    monkeypatch.setattr(
        llm_capacity, "_default_session_factory", lambda: None
    )
    monkeypatch.setattr(
        llm_capacity, "_fallback_lock", threading.BoundedSemaphore(2)
    )


def test_slot_count_follows_the_model_configuration(monkeypatch):
    monkeypatch.setenv("LLM_PARALLEL_SLOTS", "4")
    assert configured_slots() == 4
    monkeypatch.setenv("LLM_PARALLEL_SLOTS", "")
    assert configured_slots() == llm_capacity.DEFAULT_SLOTS
    # Мусор в настройке не должен ронять поиск.
    monkeypatch.setenv("LLM_PARALLEL_SLOTS", "не число")
    assert configured_slots() == llm_capacity.DEFAULT_SLOTS
    monkeypatch.setenv("LLM_PARALLEL_SLOTS", "0")
    assert configured_slots() == 1


def test_a_third_caller_waits_instead_of_failing():
    """Ожидание места безопасно, ожидание в очереди сервера — нет."""
    peak = 0
    active = 0
    lock = threading.Lock()

    def call() -> None:
        nonlocal peak, active
        with model_slot("test"):
            with lock:
                active += 1
                peak = max(peak, active)
            sleep(0.15)
            with lock:
                active -= 1

    threads = [threading.Thread(target=call) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak <= 2, "одновременно у модели не должно быть больше двух"


def test_the_slot_is_released_when_the_call_raises():
    """Иначе одна ошибка навсегда съедала бы место."""
    with pytest.raises(RuntimeError):
        with model_slot("test"):
            raise RuntimeError("сбой вызова")

    # Место свободно: следующий вызов проходит сразу.
    with model_slot("test"):
        pass
