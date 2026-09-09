"""Стадия заявки и ближайшее действие закупщика для списка «Запросы».

Статус RFQ описывает, что успела сделать система, а не что должен сделать
человек: «Сводка готова» появляется и при одном ответе из шести. Здесь из
агрегатов по рассылке, переписке и котировкам выводятся две независимые
величины:

* стадия (`stage`) — где заявка на конвейере поиск → рассылка → переписка
  → сводка. Отвечает на «далеко ли до конца»;
* ближайшее действие (`next_action`) — единственный код из закрытого
  набора, отвечающий на «нужно ли мне сейчас что-то делать».

Модуль возвращает коды, а не русские подписи: словарь подписей живёт во
фронтенде рядом с остальными (`statusLabels.ts`), чтобы формулировки
правились без выката бэкенда.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.enums import RFQStatus

# Сколько дней молчания поставщика перестают быть нормальным ожиданием.
# Пять рабочих суток — типичный срок ответа китайского и индийского
# отдела продаж с учётом разницы часовых поясов и выходных; раньше
# напоминание выглядит навязчивым, позже — заявка успевает протухнуть.
SILENCE_DAYS = 5

# Стадии конвейера — четыре сегмента полоски прогресса в строке таблицы.
STAGE_SEARCH = "search"
STAGE_DISPATCH = "dispatch"
STAGE_DIALOGUE = "dialogue"
STAGE_SUMMARY = "summary"

STAGE_ORDER = (STAGE_SEARCH, STAGE_DISPATCH, STAGE_DIALOGUE, STAGE_SUMMARY)

# Ближайшее действие. Порядок объявления совпадает с приоритетом проверок
# в `next_action`: у заявки бывает несколько поводов, показывается самый
# срочный.
ACTION_CLOSED = "closed"                  # закрыт, делать нечего
ACTION_ESCALATION = "escalation"          # открытая эскалация — нужен человек
ACTION_DISPATCH_ERROR = "dispatch_error"  # письмо не ушло
ACTION_REPLY = "reply"                    # поставщик написал и ждёт нас
ACTION_SILENCE = "silence"                # молчат дольше SILENCE_DAYS
ACTION_DECIDE = "decide"                  # данные собраны, пора сравнивать
ACTION_INCOMPLETE = "incomplete"          # ответы есть, данных не хватает
ACTION_WAITING = "waiting"                # ждём ответов, срок не вышел
ACTION_DISPATCH = "dispatch"              # поставщики найдены, не разослано
ACTION_SEARCH = "search"                  # вещество проверено, поиска нет
ACTION_VERIFY = "verify"                  # черновик, вещество не подтверждено


def as_utc(moment: datetime | None) -> datetime | None:
    """Приводит отметку времени к UTC.

    SQLite отдаёт наивные `datetime` даже для колонок `DateTime(timezone=True)`,
    PostgreSQL — осознанные. Вычитание одного из другого падает, поэтому
    наивные значения помечаются UTC: сервер всюду пишет время в UTC.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class RfqProgress:
    """Срез хода работ по одной заявке — вход для стадии и действия."""

    status: RFQStatus
    verified: bool = False
    # Найдено поиском (не исключённые вручную) и разослано.
    n_suppliers_found: int = 0
    n_recipients: int = 0
    n_dispatch_errors: int = 0
    # Компании, от которых пришёл хотя бы один ответ, и те из них,
    # чьё сообщение осталось без нашего ответа.
    n_suppliers_replied: int = 0
    n_awaiting_our_reply: int = 0
    n_open_escalations: int = 0
    n_quotations: int = 0
    completeness_pct: int = 0
    dispatched_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None

    @property
    def n_silent(self) -> int:
        """Компании, которым разослали и которые ни разу не ответили."""
        return max(0, self.n_recipients - self.n_suppliers_replied)


def waiting_days(progress: RfqProgress, *, now: datetime | None = None) -> int | None:
    """Сколько суток прошло с последнего движения по переписке.

    Точка отсчёта — последний ответ поставщика, а если ответов не было,
    момент рассылки. До рассылки ожидание не началось и величины нет.
    """
    anchor = as_utc(progress.last_inbound_at) or as_utc(progress.dispatched_at)
    if anchor is None:
        return None
    moment = as_utc(now) or datetime.now(timezone.utc)
    return max(0, (moment - anchor).days)


def rfq_stage(progress: RfqProgress) -> str:
    """Сегмент конвейера, на котором стоит заявка."""
    if progress.status == RFQStatus.CLOSED or progress.completeness_pct >= 100:
        return STAGE_SUMMARY
    if progress.n_suppliers_replied > 0 or progress.n_quotations > 0:
        return STAGE_DIALOGUE
    if progress.n_recipients > 0:
        return STAGE_DISPATCH
    return STAGE_SEARCH


def rfq_next_action(
    progress: RfqProgress, *, now: datetime | None = None
) -> str:
    """Единственное ближайшее действие закупщика.

    Проверки идут от срочного к рутинному. Молчание проверяется раньше
    готовности к решению намеренно: сводка по двум ответам из шести —
    это не сводка, и сравнивать предложения рано, пока четверых можно
    дожать напоминанием.
    """
    if progress.status == RFQStatus.CLOSED:
        return ACTION_CLOSED
    if progress.n_open_escalations > 0:
        return ACTION_ESCALATION
    if progress.n_dispatch_errors > 0:
        return ACTION_DISPATCH_ERROR
    if progress.n_awaiting_our_reply > 0:
        return ACTION_REPLY

    days = waiting_days(progress, now=now)
    if progress.n_silent > 0 and days is not None and days >= SILENCE_DAYS:
        return ACTION_SILENCE

    if progress.n_quotations > 0 and progress.completeness_pct >= 100:
        return ACTION_DECIDE
    if progress.n_quotations > 0:
        return ACTION_INCOMPLETE
    if progress.n_recipients > 0:
        return ACTION_WAITING
    if not progress.verified:
        return ACTION_VERIFY
    if progress.n_suppliers_found > 0:
        return ACTION_DISPATCH
    return ACTION_SEARCH


# Насколько действие срочно: чем меньше число, тем выше строка в списке при
# сортировке по срочности. Фронтенд сортирует теми же весами, но держать
# порядок в одном месте надёжнее — он же используется в экспорте CSV.
ACTION_URGENCY = {
    ACTION_ESCALATION: 0,
    ACTION_DISPATCH_ERROR: 1,
    ACTION_REPLY: 2,
    ACTION_SILENCE: 3,
    ACTION_DECIDE: 4,
    ACTION_DISPATCH: 5,
    ACTION_VERIFY: 6,
    ACTION_SEARCH: 7,
    ACTION_INCOMPLETE: 8,
    ACTION_WAITING: 9,
    ACTION_CLOSED: 10,
}
