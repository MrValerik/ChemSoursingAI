"""Фоновая обработка обычных и тестовых Email-ответов."""

from types import SimpleNamespace

from app import communication_email_worker as worker
from app.connectors.email import IncomingEmail
from app.services import email_workflow


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _settings(*, inbox_enabled: bool = True, test_enabled: bool = False):
    return SimpleNamespace(
        email_inbox_poll_enabled=inbox_enabled,
        email_inbox_poll_interval_s=30,
        email_inbox_poll_batch_size=100,
        communication_test_email_auto_reply_enabled=test_enabled,
        communication_test_email_poll_interval_s=30,
        communication_test_email_poll_batch_size=20,
    )


def _empty_test_summary():
    return SimpleNamespace(
        fetched=0,
        matched=0,
        processed=0,
        replied=0,
        escalated=0,
        duplicates=0,
        errors=[],
    )


def test_poll_imports_real_inbox_when_test_auto_reply_is_disabled(monkeypatch):
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(worker, "get_settings", _settings)
    monkeypatch.setattr(
        worker,
        "_poll_inbox",
        lambda *, limit, backfill_recent: calls.append(
            (limit, backfill_recent)
        )
        or True,
    )

    assert worker.poll_once(backfill_recent=True) is True
    assert calls == [(100, True)]


def test_test_poll_still_runs_when_real_inbox_fails(monkeypatch):
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: _settings(test_enabled=True),
    )
    monkeypatch.setattr(
        worker,
        "_poll_inbox",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    monkeypatch.setattr(worker, "SessionLocal", _FakeSession)
    called: list[bool] = []
    monkeypatch.setattr(
        worker,
        "sync_communication_test_email",
        lambda db, limit: called.append(True) or _empty_test_summary(),
    )

    assert worker.poll_once() is False
    assert called == [True]


def test_inbox_poll_uses_effective_live_settings_and_unseen_after_backfill(
    monkeypatch,
):
    effective = SimpleNamespace(email_delivery_mode="live")
    connector = object()
    captured: list[dict] = []
    monkeypatch.setattr(worker, "SessionLocal", _FakeSession)
    monkeypatch.setattr(
        worker,
        "effective_email_settings",
        lambda db: (effective, True, "database"),
    )
    monkeypatch.setattr(worker, "EmailConnector", lambda settings: connector)
    monkeypatch.setattr(
        worker,
        "sync_inbox",
        lambda db, **kwargs: captured.append(kwargs)
        or email_workflow.EmailSyncSummary(),
    )

    assert worker._poll_inbox(limit=77, backfill_recent=False) is True
    assert captured == [
        {"connector": connector, "limit": 77, "unseen_only": True}
    ]


def test_regular_sync_leaves_test_reply_for_test_processor(monkeypatch):
    message = IncomingEmail(
        uid="test-uid",
        message_id="<test-reply@example.com>",
        subject="Re: test conversation",
        from_address="supplier@example.com",
        to_addresses=["buyer@example.com"],
        text="Our price is USD 10/kg.",
    )

    class Connector:
        seen: list[str] = []

        def fetch_unseen(self, limit=20):
            return [message]

        def mark_seen(self, uids):
            self.seen.extend(uids)

    monkeypatch.setattr(
        email_workflow,
        "reconcile_unlinked_email_contacts",
        lambda db: 0,
    )
    monkeypatch.setattr(
        email_workflow,
        "is_communication_test_reply",
        lambda db, incoming: True,
    )
    connector = Connector()

    result = email_workflow.sync_inbox(
        _FakeSession(),
        connector=connector,
        unseen_only=True,
    )

    assert result.fetched == 1
    assert result.processed == 0
    assert result.deferred_test_messages == 1
    assert connector.seen == []
