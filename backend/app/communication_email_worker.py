"""Один worker для IMAP-ответов реальных тестовых Email-диалогов."""

from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.services.communication_test_email import sync_communication_test_email

logger = logging.getLogger(__name__)
_stop_requested = Event()


def _request_stop(*_: object) -> None:
    _stop_requested.set()


def poll_once() -> None:
    settings = get_settings()
    if not settings.communication_test_email_auto_reply_enabled:
        return
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


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    settings = get_settings()
    logger.info(
        "Communication test email worker started (enabled=%s, interval=%ss)",
        settings.communication_test_email_auto_reply_enabled,
        settings.communication_test_email_poll_interval_s,
    )
    while not _stop_requested.is_set():
        try:
            poll_once()
        except Exception:
            logger.exception("Communication test email poll failed")
        _stop_requested.wait(settings.communication_test_email_poll_interval_s)
    logger.info("Communication test email worker stopped")


if __name__ == "__main__":
    main()
