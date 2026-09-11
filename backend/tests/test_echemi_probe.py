from types import SimpleNamespace

import httpx
import pytest

from app import echemi_probe
from app.models import User
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole
from tests.test_echemi_search import env


def prepare(env, monkeypatch, role=UserRole.ADMIN):
    sessions = env[2]
    with sessions() as db:
        db.add(User(id=42, username="synthetic-operator", full_name="Operator", password_hash="unused",
                    role=role, is_active=True))
        db.commit()
    monkeypatch.setattr(echemi_probe, "SessionLocal", sessions)
    monkeypatch.setattr(echemi_probe, "get_settings", lambda: SimpleNamespace(echemi_browser_url="http://test"))


@pytest.mark.parametrize("status", [200, 409, 503])
def test_probe_saves_audited_terminal_history(env, monkeypatch, status):
    prepare(env, monkeypatch)
    sent = []
    def respond(request):
        import json
        sent.append(json.loads(request.content))
        return httpx.Response(status, json={"status": "partial", "results": [{"title": "Synthetic"}],
                                            "diagnostics": {"verification_responses": [{"verify_code": "F001"}]}})
    original = httpx.Client
    monkeypatch.setattr(echemi_probe.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    sid = echemi_probe.run(" 50-78-2 ", 42, 2)
    with env[2]() as db:
        row = db.get(EchemiSearch, sid)
        assert row.status == ("partial" if status == 200 else "failed")
        assert row.finished_at and row.author_id == 42
        assert row.diagnostics["experiment"] == "alibaba_context_v1"
        assert row.diagnostics["attempt_limit"] == 2
    assert sent == [{"query": "50-78-2", "search_id": sid, "captcha_probe_attempts": 2}]


@pytest.mark.parametrize("condition", ["buyer", "busy", "missing_user"])
def test_probe_does_not_start_without_operator_and_free_queue(env, monkeypatch, condition):
    prepare(env, monkeypatch, UserRole.BUYER if condition == "buyer" else UserRole.ADMIN)
    if condition == "busy":
        with env[2]() as db:
            db.add(EchemiSearch(author_id=42, query="Existing", status="queued"))
            db.commit()
    with pytest.raises(ValueError):
        echemi_probe.run("50-78-2", 999 if condition == "missing_user" else 42, 2)


@pytest.mark.parametrize("query,attempts", [("", 2), ("a" * 201, 2), ("test", 0), ("test", 4),
                                           ("test", True), ("test", 2.0)])
def test_probe_rejects_invalid_parameters(query, attempts):
    with pytest.raises(ValueError):
        echemi_probe.run(query, 42, attempts)
