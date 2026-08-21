import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_agent.config import ConfigError, Settings


class SettingsTests(unittest.TestCase):
    def test_empty_allowlist_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "AGENTS.md").write_text("rules", encoding="utf-8")
            env = {
                "TELEGRAM_BOT_TOKEN": "123:secret",
                "TELEGRAM_ALLOWED_USER_IDS": "",
                "TELEGRAM_PROJECT_ROOT": str(root),
                "LOCALAPPDATA": temp,
            }
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ConfigError):
                    Settings.load()

    def test_parses_numeric_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "AGENTS.md").write_text("rules", encoding="utf-8")
            env = {
                "TELEGRAM_BOT_TOKEN": "123:secret",
                "TELEGRAM_ALLOWED_USER_IDS": "101, 202",
                "TELEGRAM_PROJECT_ROOT": str(root),
                "LOCALAPPDATA": temp,
            }
            with patch.dict(os.environ, env, clear=True):
                settings = Settings.load()
            self.assertEqual(settings.allowed_user_ids, frozenset({101, 202}))
