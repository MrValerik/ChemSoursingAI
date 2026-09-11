"""Фоновый импорт обычных и тестовых Email-ответов из общего IMAP."""

from __future__ import annotations

import logging
import signal
from threading import Event

from app.connectors.email import EmailConnector
from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.services.communication_test_email import sync_communication_test_email
from app.services.email_workflow import EmailSyncSummary, sync_inbox
from app.services.integration_settings import effective_email_settings

logger = logging.getLogger(__name__)
_stop_requested = Event()


def _request_stop(*_: object) -> None:
    _stop_requested.set()


def _log_inbox_summary(summary: EmailSyncSummary) -> None:
    if summary.fetched or summary.processed or summary.errors:
        logger.info(
            "Email inbox poll: fetched=%s processed=%s duplicates=%s "
            "unmatched=%s quotations=%s drafts=%s sent=%s escalated=%s "
            "test_deferred=%s errors=%s",
            summary.fetched,
            summary.processed,
            summary.duplicates,
            summary.unmatched,
            summary.quotations_created,
            summary.followups_drafted,
            summary.followups_sent,
            summary.escalations_created,
            summary.deferred_test_messages,
            len(summary.errors),
        )
        for error in summary.errors:
            logger.warning("Email inbox poll error: %s", error)


def _poll_inbox(*, limit: int, backfill_recent: bool) -> bool:
    """Импортирует письма; False просит повторить стартовый backfill."""

    with SessionLocal() as db:
        email_settings, enabled, _ = effective_email_settings(db)
        if not enabled or email_settings.email_delivery_mode != "live":
            return True
        summary = sync_inbox(
            db,
            connector=EmailConnector(email_settings),
            limit=limit,
            unseen_only=not backfill_recent,
        )
    _log_inbox_summary(summary)
    return not summary.errors


def poll_once(*, backfill_recent: bool = False) -> bool:
    settings = get_settings()
    inbox_ok = True
    if settings.email_inbox_poll_enabled:
        try:
            inbox_ok = _poll_inbox(
                limit=settings.email_inbox_poll_batch_size,
                backfill_recent=backfill_recent,
            )
        except Exception:
            inbox_ok = False
            logger.exception("Email inbox poll failed")

    if settings.communication_test_email_auto_reply_enabled:
        try:
            with SessionLocal() as db:
                summary = sync_communication_test_email(
                    db,
                    limit=settings.communication_test_email_poll_batch_size,
                )
            if summary.processed or summary.errors:
                logger.info(
                    "Communication test email poll: fetched=%s matched=%s "
                    "processed=%s replied=%s escalated=%s duplicates=%s errors=%s",
                    summary.fetched,
                    summary.matched,
                    summary.processed,
                    summary.replied,
                    summary.escalated,
                    summary.duplicates,
                    len(summary.errors),
                )
                for error in summary.errors:
                    logger.warning("Communication test email poll error: %s", error)
        except Exception:
            logger.exception("Communication test email poll failed")
    return inbox_ok


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    settings = get_settings()
    logger.info(
        "Email worker started (inbox_enabled=%s, test_reply_enabled=%s)",
        settings.email_inbox_poll_enabled,
        settings.communication_test_email_auto_reply_enabled,
    )
    intervals = []
    if settings.email_inbox_poll_enabled:
        intervals.append(settings.email_inbox_poll_interval_s)
    if settings.communication_test_email_auto_reply_enabled:
        intervals.append(settings.communication_test_email_poll_interval_s)
    poll_interval_s = min(intervals, default=30)
    backfill_recent = settings.email_inbox_poll_enabled
    while not _stop_requested.is_set():
        inbox_ok = poll_once(backfill_recent=backfill_recent)
        if inbox_ok:
            backfill_recent = False
        _stop_requested.wait(poll_interval_s)
    logger.info("Email worker stopped")


if __name__ == "__main__":
    main()
