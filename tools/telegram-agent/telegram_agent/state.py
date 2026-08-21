from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"threads": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"threads": {}}
        return value if isinstance(value, dict) else {"threads": {}}

    def get_thread_id(self, chat_id: int) -> str | None:
        with self._lock:
            threads = self._read().get("threads", {})
            value = threads.get(str(chat_id)) if isinstance(threads, dict) else None
            return value if isinstance(value, str) and value else None

    def set_thread_id(self, chat_id: int, thread_id: str) -> None:
        with self._lock:
            state = self._read()
            threads = state.setdefault("threads", {})
            threads[str(chat_id)] = thread_id
            self._write(state)

    def clear_thread_id(self, chat_id: int) -> bool:
        with self._lock:
            state = self._read()
            threads = state.setdefault("threads", {})
            existed = str(chat_id) in threads
            threads.pop(str(chat_id), None)
            self._write(state)
            return existed

    def get_update_offset(self) -> int | None:
        with self._lock:
            value = self._read().get("telegram_update_offset")
            return value if isinstance(value, int) and value >= 0 else None

    def set_update_offset(self, offset: int) -> None:
        if offset < 0:
            raise ValueError("Telegram update offset cannot be negative")
        with self._lock:
            state = self._read()
            state["telegram_update_offset"] = offset
            self._write(state)

    def _write(self, state: dict) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
