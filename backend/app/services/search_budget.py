"""Deterministic execution budgets for supplier-search phases.

Roadmap этап 5: каждый этап поиска ограничен числом веб-запросов, загрузок
страниц, LLM-вызовов, израсходованных токенов и общим временем. Бюджет
никогда не бросает исключение: исчерпание останавливает текущий цикл и
записывает стабильный stop reason, поэтому запуск завершается безопасным
частичным результатом, а не ошибкой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from app.core.config import get_settings

STOP_QUERY_BUDGET = "query_budget_exhausted"
STOP_PAGE_BUDGET = "page_budget_exhausted"
STOP_LLM_BUDGET = "llm_budget_exhausted"
STOP_TOKEN_BUDGET = "token_budget_exhausted"
STOP_RUNTIME_BUDGET = "runtime_budget_exhausted"
STOP_COVERAGE_SUFFICIENT = "coverage_sufficient"
STOP_PLAN_EXHAUSTED = "plan_exhausted"
STOP_TARGET_REACHED = "target_reached"
STOP_CANDIDATES_EXHAUSTED = "candidates_exhausted"
STOP_BATCHES_COMPLETED = "batches_completed"


@dataclass
class SearchBudget:
    """Counts consumed operations for one search phase.

    The first refusal wins: `stop_reason` keeps the earliest exhausted limit
    so the trace explains what actually stopped the phase.
    """

    max_queries: int
    max_page_fetches: int
    max_llm_calls: int
    max_runtime_s: float
    # Потолок расхода токенов на весь запрос поиска: вход и выход вместе.
    # Ноль означает «без ограничения» и оставлен как аварийный выключатель.
    max_tokens: int = 0
    # Токены, потраченные этим же запуском поиска раньше. Поиск и проверка
    # кандидатов приходят разными HTTP-запросами к одному search_run, и
    # лимит задан на запуск целиком, а не на каждый его этап отдельно.
    carried_prompt_tokens: int = 0
    carried_completion_tokens: int = 0
    started_at: float = field(default_factory=monotonic)
    queries_used: int = 0
    page_fetches_used: int = 0
    llm_calls_used: int = 0
    stop_reason: str | None = None
    # Клиенты, чей расход относится к этому бюджету. Считать по вызовам
    # нельзя: один разрешённый вызов дробится на половины и дозапросы, и
    # счётчик вызовов их не видит, а деньги за них берут.
    _llm_clients: list[Any] = field(default_factory=list)

    @classmethod
    def from_settings(
        cls,
        *,
        carried_prompt_tokens: int = 0,
        carried_completion_tokens: int = 0,
    ) -> "SearchBudget":
        settings = get_settings()
        return cls(
            max_queries=settings.search_max_queries,
            max_page_fetches=settings.search_max_page_fetches,
            max_llm_calls=settings.search_max_llm_calls,
            max_runtime_s=settings.search_max_runtime_s,
            max_tokens=settings.search_max_tokens,
            carried_prompt_tokens=carried_prompt_tokens,
            carried_completion_tokens=carried_completion_tokens,
        )

    def count_tokens_of(self, llm: Any) -> None:
        """Относит расход клиента модели к этому бюджету.

        Клиент сам ведёт накопительные счётчики, поэтому бюджет читает их,
        а не просит вызывающий код сообщать о каждом вызове: пропущенный
        вызов означал бы неучтённые деньги.
        """
        if llm is not None and llm not in self._llm_clients:
            self._llm_clients.append(llm)

    @property
    def prompt_tokens_used(self) -> int:
        return self.carried_prompt_tokens + sum(
            int(getattr(llm, "prompt_tokens", 0) or 0)
            for llm in self._llm_clients
        )

    @property
    def completion_tokens_used(self) -> int:
        return self.carried_completion_tokens + sum(
            int(getattr(llm, "completion_tokens", 0) or 0)
            for llm in self._llm_clients
        )

    @property
    def tokens_used(self) -> int:
        return self.prompt_tokens_used + self.completion_tokens_used

    def _refuse(self, reason: str) -> str:
        if self.stop_reason is None:
            self.stop_reason = reason
        return reason

    def _runtime_refusal(self) -> str | None:
        if monotonic() - self.started_at >= self.max_runtime_s:
            return self._refuse(STOP_RUNTIME_BUDGET)
        return None

    def refuse_query(self) -> str | None:
        """Return a stop reason instead of permitting one more web query."""
        refusal = self._runtime_refusal()
        if refusal is not None:
            return refusal
        if self.queries_used >= self.max_queries:
            return self._refuse(STOP_QUERY_BUDGET)
        self.queries_used += 1
        return None

    def refuse_page_fetch(self) -> str | None:
        """Return a stop reason instead of permitting one more page fetch."""
        refusal = self._runtime_refusal()
        if refusal is not None:
            return refusal
        if self.page_fetches_used >= self.max_page_fetches:
            return self._refuse(STOP_PAGE_BUDGET)
        self.page_fetches_used += 1
        return None

    def refuse_llm_call(self) -> str | None:
        """Return a stop reason instead of permitting one more LLM call."""
        refusal = self._runtime_refusal()
        if refusal is not None:
            return refusal
        refusal = self.refuse_tokens()
        if refusal is not None:
            return refusal
        if self.llm_calls_used >= self.max_llm_calls:
            return self._refuse(STOP_LLM_BUDGET)
        self.llm_calls_used += 1
        return None

    def refuse_tokens(self) -> str | None:
        """Return a stop reason when the run has spent its token allowance.

        Проверка стоит перед вызовом, поэтому последний разрешённый вызов
        превышает потолок на свою стоимость: узнать её заранее нельзя.
        Потолок ограничивает запуск, а не отдельный вызов.
        """
        if self.max_tokens <= 0:
            return None
        if self.tokens_used >= self.max_tokens:
            return self._refuse(STOP_TOKEN_BUDGET)
        return None

    def snapshot(self) -> dict:
        """Auditable usage summary persisted with the trace."""
        return {
            "max_queries": self.max_queries,
            "queries_used": self.queries_used,
            "max_page_fetches": self.max_page_fetches,
            "page_fetches_used": self.page_fetches_used,
            "max_llm_calls": self.max_llm_calls,
            "llm_calls_used": self.llm_calls_used,
            "max_tokens": self.max_tokens,
            "tokens_used": self.tokens_used,
            "prompt_tokens_used": self.prompt_tokens_used,
            "completion_tokens_used": self.completion_tokens_used,
            "max_runtime_s": self.max_runtime_s,
            "elapsed_s": round(monotonic() - self.started_at, 3),
            "stop_reason": self.stop_reason,
        }
