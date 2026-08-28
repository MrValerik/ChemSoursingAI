"""Фоновая обработка сохранённых Email-контактов до IMAP-опроса."""

from types import SimpleNamespace

from app import communication_email_worker as worker


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def commit(self) -> None:
        self.commits += 1


def _settings():
    return SimpleNamespace(
        communication_test_email_auto_reply_enabled=True,
        communication_test_email_poll_batch_size=20,
    )


def _empty_summary():
    return SimpleNamespace(
        fetched=0,
        matched=0,
        processed=0,
        replied=0,
        escalated=0,
        duplicates=0,
        errors=[],
    )


def test_poll_reconciles_saved_contacts_before_imap(monkeypatch):
    sessions: list[_FakeSession] = []

    def session_factory():
        session = _FakeSession()
        sessions.append(session)
        return session

    calls: list[str] = []
    monkeypatch.setattr(worker, "get_settings", _settings)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(
        worker,
        "reconcile_unlinked_email_contacts",
        lambda db: calls.append("reconcile") or 1,
    )
    monkeypatch.setattr(
        worker,
        "sync_communication_test_email",
        lambda db, limit: calls.append("imap") or _empty_summary(),
    )

    worker.poll_once()

    assert calls == ["reconcile", "imap"]
    assert sessions[0].commits == 1


def test_poll_continues_to_imap_when_reconciliation_fails(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", _settings)
    monkeypatch.setattr(worker, "SessionLocal", _FakeSession)
    monkeypatch.setattr(
        worker,
        "reconcile_unlinked_email_contacts",
        lambda db: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    called: list[bool] = []
    monkeypatch.setattr(
        worker,
        "sync_communication_test_email",
        lambda db, limit: called.append(True) or _empty_summary(),
    )

    worker.poll_once()

    assert called == [True]
