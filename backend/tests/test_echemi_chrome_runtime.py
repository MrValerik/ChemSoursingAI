import asyncio
import importlib.util
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest


def module(name):
    path = Path(__file__).resolve().parents[2] / "echemi-browser" / (name + ".py")
    spec = importlib.util.spec_from_file_location("test_" + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("consumer_fails", [False, True])
def test_cdp_context_cleans_process_on_success_and_error(tmp_path, monkeypatch, consumer_fails):
    runtime = module("chrome_runtime")
    killed, connected, arguments = [], [], []
    proc = SimpleNamespace(returncode=None, pid=123)
    async def wait(): proc.returncode = 0
    proc.wait = wait
    proc.terminate = lambda: killed.append(123)
    async def spawn(*args, **kwargs):
        arguments.extend(args)
        return proc
    async def close(): connected.append("closed")
    async def send(command): connected.append(command)
    async def session(): return SimpleNamespace(send=send)
    context = object()
    async def connect(url, **kwargs):
        connected.append(url)
        return SimpleNamespace(contexts=[context], close=close, new_browser_cdp_session=session)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("ECHEMI_CHROME_SANDBOX", "true")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    monkeypatch.setenv("ECHEMI_CDP_PORT", str(port))
    if hasattr(runtime.os, "killpg"):
        monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: killed.append(pid))
    async def run():
        try:
            async with runtime.open_chrome(SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect)), tmp_path) as actual:
                assert actual is context
                if consumer_fails: raise ValueError("consumer failure")
        except ValueError:
            assert consumer_fails
    asyncio.run(run())
    assert killed == []
    assert connected == [f"http://127.0.0.1:{port}", "Browser.close", "closed"]
    assert f"--remote-debugging-port={port}" in arguments
    assert "--remote-debugging-port=0" not in arguments
    assert "--no-first-run" in arguments and "--no-sandbox" not in arguments


def test_startup_failure_does_not_reuse_stale_port(tmp_path, monkeypatch):
    runtime = module("chrome_runtime")
    (tmp_path / "DevToolsActivePort").write_text("1234\nold", encoding="utf-8")
    async def spawn(*args, **kwargs): return SimpleNamespace(returncode=1)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", spawn)
    async def run():
        with pytest.raises(RuntimeError, match="Chrome exited"):
            async with runtime.open_chrome(None, tmp_path):
                pytest.fail("Should not yield a context")
    asyncio.run(run())
    assert not (tmp_path / "DevToolsActivePort").exists()


@pytest.mark.parametrize("title,slider,heading,expected", [
    ("Verification", False, False, True),
    ("Products", True, False, True),
    ("Products", False, True, True),
    ("Products", False, False, False),
])
def test_visible_challenge_state(title, slider, heading, expected):
    state = module("page_state")
    class Locator:
        def __init__(self, visible): self.visible = visible
        @property
        def first(self): return self
        async def is_visible(self): return self.visible
    class Page:
        async def title(self): return title
        def locator(self, selector): return Locator(slider)
        def get_by_text(self, *args, **kwargs): return Locator(heading)
    assert asyncio.run(state.needs_verification(Page())) is expected


@pytest.mark.parametrize("port", ["0", "-1", "65536", "invalid"])
def test_invalid_cdp_port(tmp_path, monkeypatch, port):
    runtime = module("chrome_runtime")
    monkeypatch.setenv("ECHEMI_CDP_PORT", port)
    async def run():
        with pytest.raises(ValueError):
            async with runtime.open_chrome(None, tmp_path):
                pytest.fail("Invalid port must not launch Chrome")
    asyncio.run(run())


def test_occupied_cdp_port(tmp_path, monkeypatch):
    runtime = module("chrome_runtime")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        monkeypatch.setenv("ECHEMI_CDP_PORT", str(occupied.getsockname()[1]))
        async def run():
            with pytest.raises(OSError):
                async with runtime.open_chrome(None, tmp_path):
                    pytest.fail("Must not attach to another listener")
        asyncio.run(run())
