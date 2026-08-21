from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def load_dotenv(path: Path) -> None:
    """Load a small KEY=VALUE file without overriding process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом") from exc
    if value <= 0:
        raise ConfigError(f"{name} должен быть больше нуля")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    project_root: Path
    data_dir: Path
    poll_timeout_seconds: int
    max_image_bytes: int
    max_text_file_bytes: int
    model: str | None

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        if env_path is not None:
            load_dotenv(env_path)

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("TELEGRAM_BOT_TOKEN не задан")

        raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        if not raw_ids:
            raise ConfigError(
                "TELEGRAM_ALLOWED_USER_IDS не задан: агент намеренно не запускается "
                "с открытым доступом"
            )
        try:
            allowed = frozenset(int(part.strip()) for part in raw_ids.split(",") if part.strip())
        except ValueError as exc:
            raise ConfigError("TELEGRAM_ALLOWED_USER_IDS должен содержать только числа") from exc
        if not allowed or any(user_id <= 0 for user_id in allowed):
            raise ConfigError("TELEGRAM_ALLOWED_USER_IDS должен содержать положительные user_id")

        raw_root = os.environ.get("TELEGRAM_PROJECT_ROOT", "").strip()
        if not raw_root:
            raise ConfigError("TELEGRAM_PROJECT_ROOT не задан")
        project_root = Path(raw_root).expanduser().resolve()
        if not project_root.is_dir() or not (project_root / "AGENTS.md").is_file():
            raise ConfigError("TELEGRAM_PROJECT_ROOT не похож на checkout ChemSource AI")

        raw_data = os.environ.get("TELEGRAM_AGENT_DATA_DIR", "").strip()
        if raw_data:
            data_dir = Path(raw_data).expanduser().resolve()
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise ConfigError("LOCALAPPDATA недоступен; задайте TELEGRAM_AGENT_DATA_DIR")
            data_dir = (Path(local_app_data) / "ChemSourceAI" / "telegram-agent").resolve()

        model = os.environ.get("CODEX_MODEL", "").strip() or None
        return cls(
            bot_token=token,
            allowed_user_ids=allowed,
            project_root=project_root,
            data_dir=data_dir,
            poll_timeout_seconds=_positive_int("TELEGRAM_POLL_TIMEOUT_SECONDS", 30),
            max_image_bytes=_positive_int("TELEGRAM_MAX_IMAGE_MB", 10) * 1024 * 1024,
            max_text_file_bytes=_positive_int("TELEGRAM_MAX_TEXT_FILE_KB", 512) * 1024,
            model=model,
        )
