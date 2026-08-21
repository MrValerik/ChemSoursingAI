from __future__ import annotations

import logging
import shutil
import signal
import tempfile
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .codex_service import CodexAuthenticationError, CodexService
from .config import ConfigError, Settings
from .security import is_authorized, redact_secrets, split_telegram_text
from .state import StateStore
from .telegram_api import TelegramAPI, TelegramError


LOGGER = logging.getLogger("chemsource.telegram_agent")
HELP = """Команды:
/ask вопрос — прочитать проект и ответить без изменений
/fix задача — исправить, проверить и выполнить workflow из AGENTS.md
/status — текущая задача, ветка и состояние файлов
/stop — остановить активный turn Codex
/new — начать новую сессию Codex
/help — эта справка

Текст без команды работает как /ask. Можно приложить PNG/JPEG/WebP или небольшой
текстовый файл (.txt, .log, .md, .json, .yaml, .yml, .csv)."""

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".txt", ".log", ".md", ".json", ".yaml", ".yml", ".csv"}


@dataclass(slots=True)
class PreparedAttachment:
    image_paths: tuple[Path, ...] = ()
    text_attachments: tuple[tuple[str, str], ...] = ()
    cleanup_dir: Path | None = None

    def cleanup(self) -> None:
        if self.cleanup_dir:
            shutil.rmtree(self.cleanup_dir, ignore_errors=True)


def parse_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return "ask", stripped
    first, _, remainder = stripped.partition(" ")
    command = first[1:].split("@", 1)[0].lower()
    if command in {"ask", "fix", "status", "stop", "new", "help", "start"}:
        return command, remainder.strip()
    return "unknown", stripped


class TelegramAgent:
    def __init__(self, settings: Settings, api=None, codex=None) -> None:
        self.settings = settings
        self.api = api or TelegramAPI(settings.bot_token)
        self.state = StateStore(settings.data_dir / "state.json")
        self.codex = codex or CodexService(
            settings.project_root, self.state, settings.model
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codex-turn")
        self._future: Future | None = None
        self._stop = False

    def close(self) -> None:
        self._stop = True
        job = self.codex.active_job
        if job is not None:
            try:
                self.codex.interrupt(job.chat_id)
            except Exception:
                LOGGER.exception("Failed to interrupt Codex during shutdown")
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.codex.close()
        self.api.close()

    def stop(self, *_args) -> None:
        self._stop = True

    def run_forever(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        offset = self.state.get_update_offset()
        LOGGER.info("Telegram agent started for %d allowed user(s)", len(self.settings.allowed_user_ids))
        while not self._stop:
            try:
                for update in self.api.get_updates(offset, self.settings.poll_timeout_seconds):
                    offset = int(update["update_id"]) + 1
                    # At-most-once processing is safer for a command that may publish code.
                    self.state.set_update_offset(offset)
                    self.handle_update(update)
            except (TelegramError, OSError, ValueError) as exc:
                LOGGER.warning("Telegram polling error: %s", redact_secrets(str(exc), (self.settings.bot_token,)))
                time.sleep(3)

    def handle_update(self, update: dict) -> None:
        if not is_authorized(update, self.settings.allowed_user_ids):
            return
        message = update.get("message") or {}
        chat_id = int(message["chat"]["id"])
        text = message.get("text") or message.get("caption") or ""
        command, prompt = parse_command(text)

        if command in {"help", "start"}:
            self._send(chat_id, HELP)
            return
        if command == "status":
            job = self.codex.active_job
            active = "ожидает запуска" if self._is_busy() and job is None else "нет"
            if job:
                active = f"/{job.mode}, с {job.started_at.astimezone().strftime('%H:%M:%S')}"
            self._send(chat_id, f"Активная задача: {active}\n{self.codex.repository_status()}")
            return
        if command == "stop":
            stopped = self.codex.interrupt(chat_id)
            self._send(chat_id, "Остановка запрошена." if stopped else "Активной задачи нет.")
            return
        if command == "new":
            if self._is_busy():
                self._send(chat_id, "Сначала остановите активную задачу командой /stop.")
            else:
                self.codex.reset_thread(chat_id)
                self._send(chat_id, "Новая сессия будет создана со следующим запросом.")
            return
        if command == "unknown":
            self._send(chat_id, "Неизвестная команда. Используйте /help.")
            return
        if command not in {"ask", "fix"}:
            command = "ask"
        if self._is_busy():
            self._send(chat_id, "Сейчас выполняется другая задача. Проверьте /status или используйте /stop.")
            return

        try:
            attachment = self._prepare_attachment(message)
        except (TelegramError, ValueError, OSError) as exc:
            self._send(chat_id, f"Не удалось принять вложение: {exc}")
            return
        if not prompt and not attachment.image_paths and not attachment.text_attachments:
            attachment.cleanup()
            self._send(chat_id, f"После /{command} добавьте вопрос или задачу.")
            return

        self._send(chat_id, f"Принял /{command}. Запускаю Codex…")
        self._future = self._executor.submit(
            self.codex.run,
            chat_id,
            command,
            prompt or "Проанализируй приложенное вложение.",
            attachment.image_paths,
            attachment.text_attachments,
        )
        self._future.add_done_callback(
            lambda future: self._finish_job(chat_id, future, attachment)
        )

    def _is_busy(self) -> bool:
        future = self._future
        return bool(future is not None and not future.done()) or self.codex.active_job is not None

    def _finish_job(self, chat_id: int, future: Future, attachment: PreparedAttachment) -> None:
        try:
            response = future.result()
        except Exception as exc:
            response = "Ошибка выполнения: " + redact_secrets(
                str(exc), (self.settings.bot_token,)
            )
            LOGGER.exception("Codex job failed")
        finally:
            attachment.cleanup()
        self._send(chat_id, redact_secrets(response, (self.settings.bot_token,)))
        if self._future is future:
            self._future = None

    def _prepare_attachment(self, message: dict) -> PreparedAttachment:
        file_id: str | None = None
        display_name = "image"
        declared_size = 0
        kind: str | None = None

        photos = message.get("photo") or []
        if photos:
            photo = photos[-1]
            file_id = photo.get("file_id")
            declared_size = int(photo.get("file_size") or 0)
            display_name = "telegram-photo.jpg"
            kind = "image"
        elif isinstance(message.get("document"), dict):
            document = message["document"]
            file_id = document.get("file_id")
            declared_size = int(document.get("file_size") or 0)
            display_name = Path(document.get("file_name") or "attachment").name
            suffix = Path(display_name).suffix.lower()
            mime = str(document.get("mime_type") or "").lower()
            if suffix in IMAGE_SUFFIXES and mime.startswith("image/"):
                kind = "image"
            elif suffix in TEXT_SUFFIXES and (mime.startswith("text/") or mime in {"", "application/json"}):
                kind = "text"
            else:
                raise ValueError("поддерживаются изображения и небольшие текстовые файлы")

        if not file_id or not kind:
            return PreparedAttachment()
        max_bytes = (
            self.settings.max_image_bytes if kind == "image" else self.settings.max_text_file_bytes
        )
        if declared_size > max_bytes:
            raise ValueError("файл превышает настроенный предел размера")

        temp_root = self.settings.data_dir / "attachments" / uuid.uuid4().hex
        temp_root.mkdir(parents=True, exist_ok=False)
        suffix = Path(display_name).suffix.lower() or (".jpg" if kind == "image" else ".txt")
        destination = temp_root / f"attachment{suffix}"
        try:
            self.api.download_file(file_id, destination, max_bytes)
            if kind == "image":
                self._validate_image_signature(destination)
                return PreparedAttachment(image_paths=(destination,), cleanup_dir=temp_root)
            content = destination.read_text(encoding="utf-8", errors="replace")
            return PreparedAttachment(
                text_attachments=((display_name, content),), cleanup_dir=temp_root
            )
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    @staticmethod
    def _validate_image_signature(path: Path) -> None:
        header = path.read_bytes()[:12]
        is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = header.startswith(b"\xff\xd8\xff")
        is_webp = len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
        if not (is_png or is_jpeg or is_webp):
            raise ValueError("содержимое файла не является PNG, JPEG или WebP")

    def _send(self, chat_id: int, text: str) -> None:
        for chunk in split_telegram_text(text):
            try:
                self.api.send_message(chat_id, chunk)
            except Exception as exc:
                LOGGER.warning("Telegram send error: %s", redact_secrets(str(exc), (self.settings.bot_token,)))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        settings = Settings.load(env_path)
        agent = TelegramAgent(settings)
    except (ConfigError, CodexAuthenticationError) as exc:
        raise SystemExit(f"Ошибка конфигурации Telegram-агента: {exc}") from exc

    signal.signal(signal.SIGINT, agent.stop)
    signal.signal(signal.SIGTERM, agent.stop)
    try:
        agent.run_forever()
    finally:
        agent.close()
