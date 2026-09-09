"""Учёт израсходованных токенов: по запуску поиска и по пользователю.

Расход хранится там же, где он возник, — на этапе (`agent_runs`), поэтому
и запуск, и пользователь считаются суммой по этапам. Отдельного счётчика в
`users` нет намеренно: два независимых источника правды о деньгах
расходятся при первой же неудачной транзакции, и разошедшийся счётчик
нечем восстановить.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, SearchRun


def run_tokens(db: Session, search_run_id: int) -> tuple[int, int]:
    """Токены (вход, выход), уже потраченные этапами этого запуска.

    Нужны, чтобы лимит на запрос считался по запуску целиком: поиск и
    проверка кандидатов приходят разными HTTP-запросами к одному
    `search_run`, и каждый из них видит только свой расход.
    """
    prompt_tokens, completion_tokens = db.execute(
        select(
            func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
            func.coalesce(func.sum(AgentRun.completion_tokens), 0),
        ).where(AgentRun.search_run_id == search_run_id)
    ).one()
    return int(prompt_tokens or 0), int(completion_tokens or 0)


def user_tokens(
    db: Session,
    user_ids: Iterable[int] | None = None,
) -> dict[int, dict[str, int]]:
    """Расход по владельцам запусков: вход, выход, сумма и число запусков.

    Возвращает только тех, у кого есть запуски: кого в словаре нет, тот
    поиск ни разу не запускал. Запуск без этапов (стоит в очереди, упал до
    первого этапа) считается запуском с нулевым расходом — деньги за него
    не брали, но пользователь его сделал.
    """
    stmt = (
        select(
            SearchRun.owner_id,
            func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
            func.coalesce(func.sum(AgentRun.completion_tokens), 0),
            func.count(func.distinct(SearchRun.id)),
        )
        .outerjoin(AgentRun, AgentRun.search_run_id == SearchRun.id)
        .group_by(SearchRun.owner_id)
    )
    ids = list(user_ids) if user_ids is not None else None
    if ids is not None:
        if not ids:
            return {}
        stmt = stmt.where(SearchRun.owner_id.in_(ids))
    usage: dict[int, dict[str, int]] = {}
    for owner_id, prompt_tokens, completion_tokens, runs in db.execute(stmt):
        prompt_total = int(prompt_tokens or 0)
        completion_total = int(completion_tokens or 0)
        usage[int(owner_id)] = {
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": prompt_total + completion_total,
            "search_runs": int(runs or 0),
        }
    return usage
