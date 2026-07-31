"""Deterministic execution budgets for supplier-search phases.

Roadmap этап 5: каждый этап поиска ограничен числом веб-запросов, загрузок
страниц, LLM-вызовов и общим временем. Бюджет никогда не бросает исключение:
исчерпание останавливает текущий цикл и записывает стабильный stop reason,
поэтому запуск завершается безопасным частичным результатом, а не ошибкой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from app.core.config import get_settings

STOP_QUERY_BUDGET = "query_budget_exhausted"
STOP_PAGE_BUDGET = "page_budget_exhausted"
STOP_LLM_BUDGET = "llm_budget_exhausted"
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
    started_at: float = field(default_factory=monotonic)
    queries_used: int = 0
    page_fetches_used: int = 0
    llm_calls_used: int = 0
    stop_reason: str | None = None

    @classmethod
    def from_settings(cls) -> "SearchBudget":
        settings = get_settings()
        return cls(
            max_queries=settings.search_max_queries,
            max_page_fetches=settings.search_max_page_fetches,
            max_llm_calls=settings.search_max_llm_calls,
            max_runtime_s=settings.search_max_runtime_s,
        )

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
        if self.llm_calls_used >= self.max_llm_calls:
            return self._refuse(STOP_LLM_BUDGET)
        self.llm_calls_used += 1
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
            "max_runtime_s": self.max_runtime_s,
            "elapsed_s": round(monotonic() - self.started_at, 3),
            "stop_reason": self.stop_reason,
        }
