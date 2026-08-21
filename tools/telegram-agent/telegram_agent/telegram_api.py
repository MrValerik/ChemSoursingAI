from __future__ import annotations

from pathlib import Path

import requests


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self._token = token
        self._api_base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"

    def close(self) -> None:
        pass

    def _call(self, method: str, payload: dict, timeout: int) -> object:
        response = requests.post(
            f"{self._api_base}/{method}", data=payload, timeout=timeout
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(body.get("description") or f"Telegram {method} failed")
        return body.get("result")

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict]:
        payload: dict[str, object] = {
            "timeout": poll_timeout,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=poll_timeout + 15)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            timeout=30,
        )

    def download_file(self, file_id: str, destination: Path, max_bytes: int) -> None:
        result = self._call("getFile", {"file_id": file_id}, timeout=30)
        if not isinstance(result, dict) or not result.get("file_path"):
            raise TelegramError("Telegram не вернул путь к файлу")
        response = requests.get(
            f"{self._file_base}/{result['file_path']}", stream=True, timeout=60
        )
        response.raise_for_status()
        content_length = int(response.headers.get("content-length", "0") or 0)
        if content_length > max_bytes:
            raise TelegramError("Вложение превышает разрешённый размер")
        written = 0
        try:
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise TelegramError("Вложение превышает разрешённый размер")
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
