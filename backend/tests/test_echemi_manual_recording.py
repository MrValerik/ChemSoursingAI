"""Manual mouse audit, using synthetic input only (no external browser)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.test_echemi_manual import manual_module
from tests.test_echemi_search import env
from app import echemi_worker


def setup(monkeypatch, max_events=20000, max_sessions=10):
    module = manual_module(monkeypatch)
    event = {"mode": "manual", "status": "waiting_for_user", "recordings": []}
    page = SimpleNamespace(mouse=SimpleNamespace(move=AsyncMock(), down=AsyncMock(), up=AsyncMock()),
                           screenshot=AsyncMock(return_value=b"synthetic"), is_closed=lambda: False)
    module.active.update(id=7, waiting=True, page=page, controller=False, manual_event=event,
                         recording_budget=module.RecordingBudget(max_events, max_sessions))
    monkeypatch.setattr(module, "needs_verification", AsyncMock(return_value=True))
    return module, event, page


class Socket:
    def __init__(self, events):
        self.events = iter(events)
        self.messages = []
        self.codes = []

    async def accept(self): pass
    async def close(self, code=1000): self.codes.append(code)
    async def send_json(self, data): self.messages.append(data)
    async def send_bytes(self, data): pass
    async def receive_text(self):
        await asyncio.sleep(0)
        try:
            return json.dumps(next(self.events))
        except StopIteration:
            raise WebSocketDisconnect()


def test_records_only_valid_delivered_input_and_reconnects_separately(monkeypatch):
    module, event, page = setup(monkeypatch)
    messages = [{"type": kind, "x": 100 + i, "y": 200, "t_ms": i * 40,
                 "cookie": "private", "text": "private"} for i, kind in enumerate(["move", "down", "move", "up"])]
    async def run():
        ws = Socket(messages)
        await module.control(ws, 7)
        assert ws.messages[0]["type"] == "recording"
        first = event["recordings"][0]
        assert [e["type"] for e in first["events"]] == ["move", "down", "move", "up"]
        assert all(e["delivery"] == "delivered" and e["delivered_t_ms"] >= e["received_t_ms"] >= 0 for e in first["events"])
        assert [e["client_t_ms"] for e in first["events"]] == [0, 40, 80, 120]
        assert "private" not in json.dumps(first)
        assert first["stop_reason"] == "disconnected" and first["ended_at"]
        await module.control(Socket(messages[:1]), 7)
        assert len(event["recordings"]) == 2
        assert event["recordings"][1]["id"] != first["id"]
        assert len(first["events"]) == 4
        assert not module.active["controller"]
        assert page.mouse.move.await_count == 5
    asyncio.run(run())


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), "private", 3600001])
def test_invalid_timestamp_cannot_reach_mouse_or_audit(monkeypatch, invalid):
    module, event, page = setup(monkeypatch)
    asyncio.run(module.control(Socket([{"type": "down", "x": 1, "y": 2, "t_ms": invalid}]), 7))
    assert not event["recordings"][0]["events"]
    assert event["recordings"][0]["stop_reason"] == "connection_error"
    page.mouse.down.assert_not_awaited()


def test_budget_bounds_all_reconnections_without_blocking_control(monkeypatch):
    module, event, page = setup(monkeypatch, max_events=2, max_sessions=2)
    message = {"type": "move", "x": 1, "y": 2}
    async def run():
        for _ in range(3):
            await module.control(Socket([message, message, message]), 7)
    asyncio.run(run())
    assert len(event["recordings"]) == 2 and event["recordings_omitted"] == 1
    assert sum(len(r["events"]) for r in event["recordings"]) == 2
    assert sum(r["dropped_events"] for r in event["recordings"]) == 4
    assert all(r["limit_reached"] for r in event["recordings"])
    assert page.mouse.move.await_count == 9


def test_challenge_disappearance_stops_input_before_recording(monkeypatch):
    module, event, page = setup(monkeypatch)
    monkeypatch.setattr(module, "needs_verification", AsyncMock(return_value=False))
    asyncio.run(module.control(Socket([{"type": "down", "x": 1, "y": 2}]), 7))
    assert event["recordings"][0]["stop_reason"] == "challenge_disappeared"
    assert event["recordings"][0]["events"] == []
    page.mouse.move.assert_not_awaited()


def test_disconnect_releases_held_button(monkeypatch):
    module, event, page = setup(monkeypatch)
    asyncio.run(module.control(Socket([{"type": "down", "x": 1, "y": 2}]), 7))
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()
    assert event["recordings"][0]["events"][0]["client_t_ms"] is None


@pytest.mark.parametrize("end", ["passed", "manual_timeout", "interrupted"])
def test_waiter_finishes_controller_and_audit_before_resuming(monkeypatch, end):
    module, _, page = setup(monkeypatch)
    page.url = "https://www.echemi.com/?token=private#secret"
    async def run():
        events = []
        checked = asyncio.Event()
        finish = asyncio.Event()
        async def needs(_):
            checked.set()
            await finish.wait()
            return end != "passed"
        monkeypatch.setattr(module, "needs_verification", needs)
        waiter = asyncio.create_task(module.wait_for_human(page, events, 10, "home"))
        await checked.wait()
        class WaitingSocket(Socket):
            async def receive_text(self): await asyncio.Event().wait()
        controller = asyncio.create_task(module.control(WaitingSocket([]), 7))
        await asyncio.sleep(.01)
        if end == "interrupted": waiter.cancel()
        else:
            if end == "manual_timeout": module.active["deadline"] = 0
            finish.set()
        if end == "interrupted":
            with pytest.raises(asyncio.CancelledError): await waiter
        else:
            assert await waiter is (end == "passed")
        assert controller.done() and not module.active["controller"]
        assert events[0]["status"] == end
        assert events[0]["stage"] == "home" and events[0]["url"] == "https://www.echemi.com/"
        assert events[0]["recordings"][0]["stop_reason"] == end
        assert events[0]["recordings"][0]["ended_at"]
    asyncio.run(run())


def test_last_recording_snapshot_survives_transport_failure_without_products(env, monkeypatch):
    client, _, _ = env
    sid = client.post("/echemi-searches", json={"query": "synthetic"}).json()["id"]
    audit = {"captcha": [{"mode": "manual", "recordings": [{"id": "synthetic", "events": [{"type": "move"}]}]}]}
    def collect(query, search_id):
        echemi_worker.save_progress(search_id, {"results": [], "diagnostics": audit})
        raise ConnectionError("private")
    monkeypatch.setattr(echemi_worker, "collect_with_progress", collect)
    assert echemi_worker.run_one()
    row = client.get(f"/echemi-searches/{sid}").json()
    assert row["status"] == "failed" and not row["results"]
    assert row["diagnostics"]["captcha"] == audit["captcha"]
    assert row["diagnostics"]["error_type"] == "ConnectionError"
    assert "private" not in json.dumps(row)
