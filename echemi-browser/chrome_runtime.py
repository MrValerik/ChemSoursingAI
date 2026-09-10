"""Start the pinned Chrome independently; CDP stays inside the container."""
import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def open_chrome(playwright, profile):
    directory = Path(profile)
    directory.mkdir(parents=True, exist_ok=True)
    port_file = directory / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    args = [os.environ.get("ECHEMI_CHROME_EXECUTABLE", "/opt/chrome/chrome-linux64/chrome"),
            "--user-data-dir=" + str(directory.resolve()),
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            "--no-first-run", "--new-window", "about:blank"]
    # Container isolation is used on this VM; Chromium user namespaces are unavailable.
    if os.environ.get("ECHEMI_CHROME_SANDBOX", "false").lower() != "true":
        args.append("--no-sandbox")
    if os.environ.get("ECHEMI_HEADLESS", "false").lower() == "true":
        args.append("--headless=new")
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name != "nt")
    browser = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                raise RuntimeError("Chrome exited before CDP became available")
            if port_file.exists():
                lines = port_file.read_text(encoding="utf-8").splitlines()
                if lines and lines[0].isdigit() and 0 < int(lines[0]) < 65536:
                    browser = await playwright.chromium.connect_over_cdp(
                        "http://127.0.0.1:" + lines[0], timeout=10000)
                    break
            await asyncio.sleep(.2)
        if browser is None:
            raise TimeoutError("Chrome CDP startup timed out")
        context = browser.contexts[0]
        yield context
    finally:
        try:
            if browser:
                try:
                    session = await browser.new_browser_cdp_session()
                    await asyncio.wait_for(session.send("Browser.close"), timeout=5)
                    await asyncio.wait_for(proc.wait(), timeout=8)
                except Exception:
                    # A closing CDP socket can race the command acknowledgement.
                    if proc.returncode is None:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=3)
                        except asyncio.TimeoutError:
                            pass
                finally:
                    await asyncio.wait_for(browser.close(), timeout=5)
        finally:
            if proc.returncode is None:
                try:
                    if os.name == "nt":
                        proc.terminate()
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    if os.name == "nt":
                        proc.kill()
                    else:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await proc.wait()
