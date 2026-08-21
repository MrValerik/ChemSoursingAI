import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telegram_agent.codex_service import CodexAuthenticationError, CodexService
from telegram_agent.state import StateStore


class FakeHandle:
    def __init__(self):
        self.interrupted = False

    def run(self):
        return SimpleNamespace(final_response="готово")

    def interrupt(self):
        self.interrupted = True


class FakeThread:
    def __init__(self, thread_id):
        self.id = thread_id
        self.turn_kwargs = None

    def turn(self, inputs, **kwargs):
        self.inputs = inputs
        self.turn_kwargs = kwargs
        return FakeHandle()


class FakeCodex:
    def __init__(self, _config):
        self.thread = FakeThread("thread-1")
        self.started_kwargs = None
        self.resumed = []

    def account(self):
        return SimpleNamespace(account=object())

    def thread_start(self, **kwargs):
        self.started_kwargs = kwargs
        return self.thread

    def thread_resume(self, thread_id, **kwargs):
        self.resumed.append((thread_id, kwargs))
        return self.thread

    def close(self):
        pass


class FakeSDK:
    Sandbox = SimpleNamespace(read_only="read-only", workspace_write="workspace-write")
    ApprovalMode = SimpleNamespace(deny_all="deny-all", auto_review="auto-review")
    TextInput = lambda self, text: ("text", text)
    LocalImageInput = lambda self, path: ("image", path)

    def __init__(self):
        self.instance = None

    def CodexConfig(self, **kwargs):
        return kwargs

    def Codex(self, config):
        self.instance = FakeCodex(config)
        return self.instance


class CodexServiceTests(unittest.TestCase):
    def test_ask_is_read_only_and_thread_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            sdk = FakeSDK()
            root = Path(temp)
            state = StateStore(root / "state.json")
            service = CodexService(root, state, sdk=sdk)
            self.assertEqual(service.run(7, "ask", "вопрос"), "готово")
            self.assertEqual(sdk.instance.thread.turn_kwargs["sandbox"], "read-only")
            self.assertEqual(state.get_thread_id(7), "thread-1")
            service.run(7, "fix", "исправь")
            self.assertEqual(sdk.instance.resumed[0][0], "thread-1")
            self.assertEqual(sdk.instance.thread.turn_kwargs["sandbox"], "workspace-write")

    def test_unauthenticated_sdk_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            sdk = FakeSDK()
            original_codex = sdk.Codex

            def unauthenticated_codex(config):
                instance = original_codex(config)
                instance.account = lambda: SimpleNamespace(account=None)
                return instance

            sdk.Codex = unauthenticated_codex
            with self.assertRaises(CodexAuthenticationError):
                CodexService(Path(temp), StateStore(Path(temp) / "state.json"), sdk=sdk)
