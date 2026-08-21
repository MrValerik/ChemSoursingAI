import unittest

from telegram_agent.app import parse_command
from telegram_agent.security import is_authorized, redact_secrets, split_telegram_text


class SecurityTests(unittest.TestCase):
    def test_only_private_allowed_user_is_authorized(self):
        update = {"message": {"chat": {"type": "private"}, "from": {"id": 42}}}
        self.assertTrue(is_authorized(update, frozenset({42})))
        update["message"]["chat"]["type"] = "group"
        self.assertFalse(is_authorized(update, frozenset({42})))

    def test_redacts_tokens_and_secret_assignments(self):
        text = "TELEGRAM_BOT_TOKEN=123456789:abcdefghijklmnopqrstuvwxyzABCDE\nsk-demo_secret_123456789"
        result = redact_secrets(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", result)
        self.assertNotIn("sk-demo", result)

    def test_command_parsing_and_plain_text(self):
        self.assertEqual(parse_command("почему упал тест?"), ("ask", "почему упал тест?"))
        self.assertEqual(parse_command("/fix@my_bot исправь API"), ("fix", "исправь API"))

    def test_telegram_chunks_stay_within_limit(self):
        chunks = split_telegram_text("x" * 9000, limit=1000)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))
        self.assertEqual("".join(chunks), "x" * 9000)
