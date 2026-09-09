"""Стадия заявки и ближайшее действие закупщика (список «Запросы»).

Проверяется чистая логика вывода: у заявки бывает сразу несколько поводов
что-то сделать, и в строке должен оказаться самый срочный.
"""

from datetime import datetime, timedelta, timezone

from app.models.enums import RFQStatus
from app.services.rfq_progress import (
    ACTION_CLOSED,
    ACTION_DECIDE,
    ACTION_DISPATCH,
    ACTION_DISPATCH_ERROR,
    ACTION_ESCALATION,
    ACTION_INCOMPLETE,
    ACTION_REPLY,
    ACTION_SEARCH,
    ACTION_SILENCE,
    ACTION_VERIFY,
    ACTION_WAITING,
    SILENCE_DAYS,
    STAGE_DIALOGUE,
    STAGE_DISPATCH,
    STAGE_SEARCH,
    STAGE_SUMMARY,
    RfqProgress,
    as_utc,
    rfq_next_action,
    rfq_stage,
    waiting_days,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_draft_asks_to_verify_substance():
    progress = RfqProgress(status=RFQStatus.DRAFT, verified=False)
    assert rfq_next_action(progress, now=NOW) == ACTION_VERIFY
    assert rfq_stage(progress) == STAGE_SEARCH


def test_verified_without_suppliers_asks_to_search():
    progress = RfqProgress(status=RFQStatus.VERIFIED, verified=True)
    assert rfq_next_action(progress, now=NOW) == ACTION_SEARCH


def test_found_suppliers_but_nothing_sent_asks_to_dispatch():
    progress = RfqProgress(
        status=RFQStatus.VERIFIED, verified=True, n_suppliers_found=7
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_DISPATCH


def test_fresh_dispatch_is_plain_waiting():
    progress = RfqProgress(
        status=RFQStatus.SENT,
        verified=True,
        n_suppliers_found=6,
        n_recipients=6,
        dispatched_at=_ago(1),
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_WAITING
    assert rfq_stage(progress) == STAGE_DISPATCH
    assert waiting_days(progress, now=NOW) == 1


def test_silence_appears_only_after_threshold():
    base = dict(
        status=RFQStatus.SENT,
        verified=True,
        n_recipients=6,
        dispatched_at=_ago(SILENCE_DAYS - 1),
    )
    assert rfq_next_action(RfqProgress(**base), now=NOW) == ACTION_WAITING

    base["dispatched_at"] = _ago(SILENCE_DAYS)
    assert rfq_next_action(RfqProgress(**base), now=NOW) == ACTION_SILENCE


def test_answer_resets_the_silence_clock():
    """Ответ одной компании отодвигает точку отсчёта молчания остальных."""
    progress = RfqProgress(
        status=RFQStatus.COLLECTING,
        verified=True,
        n_recipients=6,
        n_suppliers_replied=1,
        n_quotations=1,
        completeness_pct=100,
        dispatched_at=_ago(SILENCE_DAYS + 3),
        last_inbound_at=_ago(1),
        last_outbound_at=_ago(1),
    )
    assert waiting_days(progress, now=NOW) == 1
    assert rfq_next_action(progress, now=NOW) == ACTION_DECIDE


def test_thin_coverage_asks_to_remind_before_deciding():
    """Полная котировка от одного из шести — ещё не повод сравнивать."""
    progress = RfqProgress(
        status=RFQStatus.SUMMARIZED,
        verified=True,
        n_recipients=6,
        n_suppliers_replied=1,
        n_quotations=1,
        completeness_pct=100,
        dispatched_at=_ago(10),
        last_inbound_at=_ago(SILENCE_DAYS + 1),
        last_outbound_at=_ago(SILENCE_DAYS + 1),
    )
    assert progress.n_silent == 5
    assert rfq_next_action(progress, now=NOW) == ACTION_SILENCE
    assert rfq_stage(progress) == STAGE_SUMMARY


def test_incoming_message_outranks_silence():
    progress = RfqProgress(
        status=RFQStatus.COLLECTING,
        verified=True,
        n_recipients=6,
        n_suppliers_replied=2,
        n_awaiting_our_reply=1,
        n_quotations=2,
        completeness_pct=50,
        dispatched_at=_ago(20),
        last_inbound_at=_ago(SILENCE_DAYS + 2),
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_REPLY


def test_delivery_error_outranks_conversation():
    progress = RfqProgress(
        status=RFQStatus.SENT,
        verified=True,
        n_recipients=6,
        n_dispatch_errors=2,
        n_suppliers_replied=1,
        n_awaiting_our_reply=1,
        dispatched_at=_ago(1),
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_DISPATCH_ERROR


def test_escalation_outranks_everything_but_closure():
    progress = RfqProgress(
        status=RFQStatus.ESCALATED,
        verified=True,
        n_recipients=6,
        n_dispatch_errors=1,
        n_open_escalations=1,
        n_awaiting_our_reply=2,
        dispatched_at=_ago(1),
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_ESCALATION

    closed = RfqProgress(
        status=RFQStatus.CLOSED, verified=True, n_open_escalations=1
    )
    assert rfq_next_action(closed, now=NOW) == ACTION_CLOSED


def test_incomplete_quotes_ask_for_missing_data():
    progress = RfqProgress(
        status=RFQStatus.PARSED,
        verified=True,
        n_recipients=4,
        n_suppliers_replied=4,
        n_quotations=4,
        completeness_pct=50,
        dispatched_at=_ago(3),
        last_inbound_at=_ago(1),
        last_outbound_at=NOW,
    )
    assert rfq_next_action(progress, now=NOW) == ACTION_INCOMPLETE
    assert rfq_stage(progress) == STAGE_DIALOGUE


def test_naive_timestamps_are_read_as_utc():
    """SQLite отдаёт время без зоны — вычитание не должно падать."""
    progress = RfqProgress(
        status=RFQStatus.SENT,
        verified=True,
        n_recipients=2,
        dispatched_at=datetime(2026, 9, 5, 12, 0),
    )
    assert as_utc(progress.dispatched_at).tzinfo is timezone.utc
    assert waiting_days(progress, now=NOW) == 4


def test_waiting_days_absent_before_dispatch():
    assert waiting_days(RfqProgress(status=RFQStatus.DRAFT), now=NOW) is None
