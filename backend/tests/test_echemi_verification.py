import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest


def module(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "echemi-browser"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("echemi_verification_test", root / "verification.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_recorded_path_uses_actual_distance_and_keeps_endpoint(monkeypatch):
    m = module(monkeypatch)
    raw = json.loads(Path(m.__file__).with_name("trajectory.json").read_text())
    points = m.sampled_trajectory(raw)
    assert len(points) < len(raw)
    assert points[-1] == raw[-1]
    path = m.scaled_trajectory(points, {"x": 200, "y": 300, "width": 40, "height": 40},
                               {"width": 320}, {"w": 1280, "h": 900})
    assert path[-1][1] - path[0][1] == pytest.approx(280)
    assert path[-1][0] == 2.4775


@pytest.mark.parametrize("points", [[], [[0,0,0]], [[0,0,0],[1,0,1]],
    [[0,0,0],[2,1,1],[1,2,2]], [[0,0,0],[1,float("nan"),2]], [[0,0,0],[True,2,2]]])
def test_invalid_recording_rejected(monkeypatch, points):
    with pytest.raises(ValueError):
        module(monkeypatch).sampled_trajectory(points)


def browser_app(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "echemi-browser"
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(async_playwright=None))
    spec = importlib.util.spec_from_file_location("echemi_ready_test", root / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("enabled,visible,accepted,expected_calls", [
    (True, False, False, []), (True, True, True, ["automatic"]),
    (True, True, False, ["automatic", "manual"]), (False, True, False, ["manual"]),
])
def test_ready_keeps_manual_fallback(monkeypatch, enabled, visible, accepted, expected_calls):
    m = browser_app(monkeypatch)
    calls = []
    async def needs(page): return visible
    async def automatic(*args): calls.append("automatic"); return accepted
    async def manual(*args): calls.append("manual"); return True
    monkeypatch.setattr(m, "needs_verification", needs)
    monkeypatch.setattr(m, "attempt_slider", automatic)
    monkeypatch.setattr(m, "wait_for_human", manual)
    monkeypatch.setenv("ECHEMI_AUTO_VERIFY", str(enabled).lower())
    assert asyncio.run(m.ready(None, None, []))
    assert calls == expected_calls


def test_rejected_verification_requests_restart_before_manual(monkeypatch):
    m = browser_app(monkeypatch)
    async def needs(page): return True
    async def reject(*args): return False
    async def manual(*args): raise AssertionError("Manual wait must follow last attempt only")
    monkeypatch.setattr(m, "needs_verification", needs)
    monkeypatch.setattr(m, "attempt_slider", reject)
    monkeypatch.setattr(m, "wait_for_human", manual)
    monkeypatch.setenv("ECHEMI_AUTO_VERIFY", "true")
    with pytest.raises(m.RestartSearch):
        asyncio.run(m.ready(None, None, [], can_restart=True))


@pytest.mark.parametrize("succeed_on", [1,2,3])
def test_collect_retries_without_losing_saved_results(monkeypatch, succeed_on):
    m = browser_app(monkeypatch)
    monkeypatch.setenv("ECHEMI_AUTO_ATTEMPTS", "3")
    calls = []
    async def run_once(query, output, can_restart):
        calls.append(can_restart)
        if len(calls)==1:
            output["results"].extend([{"product_url":"first","detail_status":"read","price_text":"$10"},
                                     {"product_url":"second","detail_status":"reading"}])
        else:
            assert output["results"][0]["price_text"] == "$10"
            assert output["results"][0]["detail_status"] == "read"
            assert output["results"][1]["detail_status"] == "pending"
        if len(calls)<succeed_on: raise m.RestartSearch()
        output["status"]="completed"
    async def pause(seconds): pass
    monkeypatch.setattr(m, "collect_once", run_once)
    monkeypatch.setattr(m.asyncio, "sleep", pause)
    output={"results":[],"diagnostics":{"captcha":[]}}
    asyncio.run(m.collect("query",output))
    assert len(calls)==succeed_on
    assert calls==[True,True,False][:succeed_on]
    assert len(output["results"])==2
    assert len(output["diagnostics"].get("restarts",[]))==succeed_on-1


@pytest.mark.parametrize("configured,expected", [(0,1),(1,1),(3,3),(99,3)])
def test_retry_limit_ends_with_manual_run(monkeypatch, configured, expected):
    m = browser_app(monkeypatch)
    monkeypatch.setenv("ECHEMI_AUTO_ATTEMPTS", str(configured))
    calls=[]
    async def run_once(query, output, can_restart):
        calls.append(can_restart)
        if can_restart: raise m.RestartSearch()
        output["status"]="blocked"
    async def pause(seconds): pass
    monkeypatch.setattr(m, "collect_once", run_once)
    monkeypatch.setattr(m.asyncio, "sleep", pause)
    output={"results":[],"diagnostics":{"captcha":[]}}
    asyncio.run(m.collect("query",output))
    assert len(calls)==expected and calls[-1] is False
    assert output["status"]=="blocked"


def test_reentry_search_does_not_duplicate_or_reread_saved_card(monkeypatch):
    m = browser_app(monkeypatch)
    url="https://www.echemi.com/produce/synthetic.html"
    opened=[]
    async def noop(*args, **kwargs): pass
    async def box(): return None
    async def evaluate(script): return [{"url":url}] if script==m._BLOCKS else "synthetic browser"
    async def goto(target, **kwargs): opened.append(target)
    page=SimpleNamespace(bring_to_front=noop,set_viewport_size=noop,set_default_timeout=lambda _:None,
        evaluate=evaluate,on=lambda *args:None,goto=goto,
        locator=lambda _:SimpleNamespace(bounding_box=box,fill=noop,press_sequentially=noop,press=noop))
    @asynccontextmanager
    async def playwright(): yield None
    @asynccontextmanager
    async def chrome(*args): yield SimpleNamespace(pages=[page])
    async def ready(*args): return True
    monkeypatch.setattr(m,"async_playwright",playwright)
    monkeypatch.setattr(m,"open_chrome",chrome)
    monkeypatch.setattr(m,"ready",ready)
    monkeypatch.setattr(m,"Mouse",lambda _:SimpleNamespace(go=noop))
    monkeypatch.setattr(m.asyncio,"sleep",noop)
    saved={"product_url":url,"detail_status":"read","price_text":"$10"}
    output={"results":[saved],"diagnostics":{"captcha":[]}}
    asyncio.run(m.collect_once("query",output,can_restart=True))
    assert output["results"]==[saved]
    assert opened==["https://www.echemi.com/"]
    assert output["status"]=="completed"


def test_path_cannot_leave_viewport(monkeypatch):
    with pytest.raises(ValueError):
        module(monkeypatch).scaled_trajectory([[0,0,0],[1,100,0]],
            {"x": 1000, "y": 10, "width": 40, "height": 40}, {"width": 320}, {"w": 1280, "h": 900})


class Page:
    url = "https://www.echemi.com/?private=secret"

    def __init__(self, code="T001", accepted=True, mode="normal"):
        self.code, self.accepted, self.mode = code, accepted, mode
        self.listeners = {}
        self.mouse = self
        self.up_count = 0
        self.started = asyncio.Event()

    def on(self, name, callback): self.listeners[name] = callback
    def remove_listener(self, name, callback): self.listeners.pop(name)
    def is_closed(self): return False
    async def evaluate(self, expression): return {"w": 1280, "h": 900}

    def locator(self, selector):
        width = 320 if selector.endswith("body") else 40
        async def noop(**kwargs): pass
        async def box(): return {"x": 200, "y": 300, "width": width, "height": 40}
        return SimpleNamespace(wait_for=noop, scroll_into_view_if_needed=noop, bounding_box=box)

    async def down(self): self.started.set()
    async def move(self, x, y):
        if self.mode == "error": raise RuntimeError("move failed")
        if self.mode in {"timeout", "cancel"}: await asyncio.Event().wait()

    async def up(self):
        self.up_count += 1
        async def payload(): return {"Result": {"VerifyCode": self.code, "VerifyResult": self.accepted}, "token": "secret"}
        if self.code and "response" in self.listeners:
            await self.listeners["response"](SimpleNamespace(
                url="https://captcha.ap-southeast-1.aliyuncs.com/", json=payload))


def setup(monkeypatch, visible=False):
    m = module(monkeypatch)
    real_sleep = asyncio.sleep
    async def sleep(_): await real_sleep(0)
    async def needs(page): return visible
    monkeypatch.setattr(m.asyncio, "sleep", sleep)
    monkeypatch.setattr(m, "needs_verification", needs)
    async def go(*args): pass
    return m, SimpleNamespace(go=go, x=0, y=0)


@pytest.mark.parametrize("code,accepted,visible,expected", [
    ("T001", True, False, True), ("T001", True, True, False),
    ("F001", False, False, False), (None, False, False, False),
])
def test_acceptance_requires_provider_and_unblocked_page(monkeypatch, code, accepted, visible, expected):
    m, mouse = setup(monkeypatch, visible)
    async def run():
        page, events = Page(code, accepted), []
        assert await m.attempt_slider(page, mouse, events) is expected
        assert events[0]["mode"] == "automatic"
        assert events[0]["url"] == "https://www.echemi.com/"
        assert "secret" not in json.dumps(events)
        assert not page.listeners
        assert page.up_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["error", "timeout", "cancel"])
def test_failure_and_cancellation_release_button_and_listener(monkeypatch, mode):
    m, mouse = setup(monkeypatch)
    async def run():
        page, events = Page(mode=mode), []
        task = asyncio.create_task(m.attempt_slider(page, mouse, events, timeout=.05))
        if mode == "cancel":
            await page.started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert events[0]["status"] == "cancelled"
        else:
            assert not await task
            assert events[0]["status"] == "not_passed"
        assert page.up_count == 1
        assert not page.listeners
    asyncio.run(run())
