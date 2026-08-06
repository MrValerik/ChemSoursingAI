"""Расход токенов принадлежит этапу, а не всему клиенту.

Оценка и аудит работают на одном экземпляре LLMClient, а счётчик токенов
живёт вместе с клиентом. Запись полного счётчика приписывала аудиту ещё и
расход оценки: на прогоне 55 по адипиновой кислоте у аудита стояло 21 778
входных токенов вместо своих 8 658, и по трассе выходило, будто самый
дорогой этап — именно он. Вывод был неверным.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_token_accounting.db")

from time import monotonic

from app.extraction.llm_client import LLMClient
from app.models.search_trace import AgentRun
from app.services.search_trace import finish_agent_run, utc_now


class _Llm:
    """Клиент со счётчиком, который живёт дольше одного этапа.

    Настоящий ``LLMClient`` в конструкторе требует настроек провайдера,
    поэтому здесь берётся только его учёт токенов — то, что проверяется.
    """

    take_usage = LLMClient.take_usage

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._taken_prompt_tokens = 0
        self._taken_completion_tokens = 0

    def spend(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion


def _stage() -> AgentRun:
    return AgentRun(
        search_run_id=1,
        sequence=1,
        agent_slug="stage",
        agent_name="Этап",
        started_at=utc_now(),
    )


def test_the_second_stage_is_charged_only_its_own_calls():
    llm = _Llm()

    llm.spend(13120, 5369)
    qualification = _stage()
    finish_agent_run(qualification, monotonic(), llm=llm)

    llm.spend(8658, 1565)
    audit = _stage()
    finish_agent_run(audit, monotonic(), llm=llm)

    assert qualification.prompt_tokens == 13120
    assert qualification.completion_tokens == 5369
    assert audit.prompt_tokens == 8658
    assert audit.completion_tokens == 1565


def test_a_stage_without_calls_records_nothing():
    """Иначе детерминированный этап показал бы чужой расход."""
    llm = _Llm()
    llm.spend(500, 100)
    finish_agent_run(_stage(), monotonic(), llm=llm)

    idle = _stage()
    finish_agent_run(idle, monotonic(), llm=llm)

    assert idle.prompt_tokens is None
    assert idle.completion_tokens is None


def test_the_sum_of_stages_is_the_run_total():
    """Шапка трассы складывает этапы — двойного счёта быть не должно."""
    llm = _Llm()
    stages = []
    for prompt, completion in ((564, 278), (679, 578), (13120, 5369)):
        llm.spend(prompt, completion)
        stage = _stage()
        finish_agent_run(stage, monotonic(), llm=llm)
        stages.append(stage)

    assert sum(s.prompt_tokens for s in stages) == llm.prompt_tokens
    assert sum(s.completion_tokens for s in stages) == llm.completion_tokens
