"""Ответ, оборванный лимитом выхода, — не отказ модели.

Замер на облачной Qwen3.6: пакет оценки с 9789 символами входа упёрся в
лимит выхода, JSON оборвался незакрытой строкой на 5576-м символе,
схема-ремонт сломался там же, и этап отчитался «локальная ИИ-модель
недоступна». Сообщение неверно дважды: модель не локальная и доступна —
все 41 вызов того прогона вернули 200.

Соседние пакеты того же прогона на 5626 и 5093 символах прошли сразу.
Значит помогает не повтор, а более короткий вход.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_output_truncation.db")

import pytest

from app.api.supplier_search import _qualify_batch
from app.extraction.llm_client import (
    LLMOutputTruncatedError,
    LLMUnavailableError,
    LLMClient,
)


# --- распознавание обрыва ---


def test_a_truncated_answer_is_not_reported_as_unavailable():
    """Иначе закупщику скажут, что модели нет, хотя она ответила."""
    answer = {
        "choices": [{"finish_reason": "length", "message": {"content": "{\"res"}}],
        "usage": {"completion_tokens": 1536},
    }
    with pytest.raises(LLMOutputTruncatedError) as exc:
        LLMClient._raise_if_truncated(answer)
    assert "1536" in str(exc.value)
    assert not isinstance(exc.value, LLMUnavailableError)


def test_a_complete_answer_passes():
    answer = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
    assert LLMClient._raise_if_truncated(answer) is None


# --- дробление пакета ---


class _Llm:
    """Модель, которая обрывается на пакетах длиннее заданного."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.calls: list[int] = []
        self.last_attempts: list[dict] = []

    def generate_json(self, *, user_text: str, **kw) -> dict:
        import json

        sources = json.loads(user_text)["sources"]
        self.calls.append(len(sources))
        if len(sources) > self.limit:
            raise LLMOutputTruncatedError("не поместился")
        return {"results": [{"result_index": s["result_index"]} for s in sources]}


def _payload(count: int) -> dict:
    return {
        "chemical": {"name": "x"},
        "sources": [{"result_index": i} for i in range(count)],
        "batch_instruction": "",
    }


def test_a_batch_that_does_not_fit_is_split_and_merged():
    llm = _Llm(limit=1)
    splits: list[int] = []

    out = _qualify_batch(
        llm,
        system_prompt="p",
        batch_payload=_payload(2),
        on_split=splits.append,
    )

    assert [r["result_index"] for r in out["results"]] == [0, 1]
    # Сначала пакет целиком, потом две половины по одному.
    assert llm.calls == [2, 1, 1]
    assert splits == [1]


def test_a_single_source_that_does_not_fit_is_reported():
    """Дробить нечего — надо честно сказать, а не молчать."""
    llm = _Llm(limit=0)
    with pytest.raises(LLMOutputTruncatedError):
        _qualify_batch(llm, system_prompt="p", batch_payload=_payload(1))


def test_a_batch_that_fits_is_not_split():
    llm = _Llm(limit=5)
    splits: list[int] = []

    out = _qualify_batch(
        llm, system_prompt="p", batch_payload=_payload(2), on_split=splits.append
    )

    assert len(out["results"]) == 2
    assert llm.calls == [2]
    assert splits == []
