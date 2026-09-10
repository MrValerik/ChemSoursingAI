import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def runner():
    path = Path(__file__).resolve().parents[2] / "echemi-browser" / "job_lifecycle.py"
    spec = importlib.util.spec_from_file_location("echemi_job_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_connected


@pytest.mark.parametrize("mode", ["success", "error", "disconnect", "timeout", "cancel"])
def test_job_lifecycle_always_finishes_cleanup(mode):
    cleaned = []
    async def run():
        started = asyncio.Event()
        async def operation():
            try:
                started.set()
                if mode == "success":
                    return 42
                if mode == "error":
                    raise ValueError("collect failed")
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(True)
        async def disconnected():
            await started.wait()
            return mode == "disconnect"
        task = asyncio.create_task(runner()(
            SimpleNamespace(is_disconnected=disconnected), operation(), timeout=.05))
        if mode == "cancel":
            await started.wait()
            task.cancel()
        if mode == "success":
            assert await task == 42
        else:
            expected = {"error": ValueError, "disconnect": ConnectionError,
                        "timeout": asyncio.TimeoutError, "cancel": asyncio.CancelledError}[mode]
            with pytest.raises(expected):
                await task
        assert cleaned == [True]
    asyncio.run(run())
