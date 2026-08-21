import tempfile
import unittest
from pathlib import Path

from telegram_agent.state import StateStore


class StateStoreTests(unittest.TestCase):
    def test_thread_id_persists_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            store = StateStore(path)
            store.set_thread_id(10, "thread-1")
            self.assertEqual(StateStore(path).get_thread_id(10), "thread-1")
            self.assertTrue(store.clear_thread_id(10))
            self.assertIsNone(store.get_thread_id(10))

    def test_telegram_update_offset_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            store = StateStore(path)
            store.set_update_offset(123)
            self.assertEqual(StateStore(path).get_update_offset(), 123)
