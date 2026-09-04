"""Короткий, но правильный ответ модели не считается полным.

Замер 3 сентября 2026. Один и тот же пакет из двух страниц, отправленный
трижды подряд: пакет из прогона 333 модель вернула одной оценкой и дважды
оборвалась, пакет из прогона 342 — по две все три раза. Ответ стоит у самого
потолка вывода в 1536 токенов, и один и тот же дефицит выходит то обрывом,
то молчаливой недостачей.

Обрыв ловился и раньше — пакет делился пополам. Недостача не ловилась
никем: JSON приходил безупречный, просто с одной оценкой вместо двух, и
закупщик получал карточку «Автоматическая оценка не получена» с нулём
баллов. За прогоны 342-348 так потерялось шесть карточек.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_batch_recovery.db")

import pytest  # noqa: E402

from app.api.supplier_search import _batch_with_halving  # noqa: E402
from app.extraction.llm_client import LLMOutputTruncatedError  # noqa: E402


class _Model:
    """Модель, отвечающая по заранее заданному сценарию."""

    def __init__(self, script):
        self.script = list(script)
        self.asked: list[list[int]] = []

    def generate_json(self, *, user_text, **kwargs):
        import json

        payload = json.loads(user_text)
        indexes = [item["result_index"] for item in payload["sources"]]
        self.asked.append(indexes)
        answer = self.script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _payload(*indexes):
    return {
        "chemical": {"name": "Menthyl lactate"},
        "sources": [{"result_index": i, "page_text": "…"} for i in indexes],
    }


def _call(model, payload, **kw):
    return _batch_with_halving(
        model,
        system_prompt="prompt",
        batch_payload=payload,
        items_key="sources",
        schema_name="supplier_qualification",
        json_schema={},
        **kw,
    )


def test_недостающая_оценка_дозапрашивается():
    model = _Model([
        {"results": [{"result_index": 1, "company_name": "A"}]},
        {"results": [{"result_index": 2, "company_name": "B"}]},
    ])
    seen: list[list[int]] = []

    answer = _call(model, _payload(1, 2), on_missing=seen.append)

    assert [r["result_index"] for r in answer["results"]] == [1, 2]
    assert model.asked == [[1, 2], [2]]
    assert seen == [[2]]


def test_полный_ответ_лишнего_вопроса_не_вызывает():
    model = _Model([
        {
            "results": [
                {"result_index": 1, "company_name": "A"},
                {"result_index": 2, "company_name": "B"},
            ]
        }
    ])

    answer = _call(model, _payload(1, 2))

    assert len(answer["results"]) == 2
    assert model.asked == [[1, 2]]


def test_молчание_об_одном_источнике_не_зацикливается():
    """Дробить один элемент нечего: вопрос сюда же не возвращается."""
    model = _Model([{"results": []}])

    answer = _call(model, _payload(7))

    assert answer["results"] == []
    assert model.asked == [[7]]


def test_отказ_на_дозапросе_оставляет_то_что_есть():
    model = _Model([
        {"results": [{"result_index": 1, "company_name": "A"}]},
        RuntimeError("модель недоступна"),
    ])

    answer = _call(model, _payload(1, 2))

    assert [r["result_index"] for r in answer["results"]] == [1]


def test_обрыв_по_прежнему_дробит_пакет():
    model = _Model([
        LLMOutputTruncatedError("не поместился"),
        {"results": [{"result_index": 1, "company_name": "A"}]},
        {"results": [{"result_index": 2, "company_name": "B"}]},
    ])
    splits: list[int] = []

    answer = _call(model, _payload(1, 2), on_split=splits.append)

    assert [r["result_index"] for r in answer["results"]] == [1, 2]
    assert splits == [1]


def test_недостача_в_половине_пакета_тоже_дозапрашивается():
    model = _Model([
        LLMOutputTruncatedError("не поместился"),
        {"results": [{"result_index": 1, "company_name": "A"}]},
        # Вторая половина из двух источников отвечает про один.
        {"results": [{"result_index": 3, "company_name": "C"}]},
        {"results": [{"result_index": 4, "company_name": "D"}]},
    ])

    answer = _call(model, _payload(1, 3, 4))

    assert sorted(r["result_index"] for r in answer["results"]) == [1, 3, 4]


def test_чужой_номер_в_ответе_не_подменяет_недостающий():
    """Ответ не о том источнике, о котором спрашивали, не засчитывается."""
    model = _Model([
        {"results": [{"result_index": 1, "company_name": "A"}]},
        {"results": [{"result_index": 1, "company_name": "A ещё раз"}]},
    ])

    answer = _call(model, _payload(1, 2))

    assert [r["result_index"] for r in answer["results"]] == [1]


def test_обрыв_на_единственном_источнике_остаётся_ошибкой():
    model = _Model([LLMOutputTruncatedError("не поместился")])

    with pytest.raises(LLMOutputTruncatedError):
        _call(model, _payload(5))
