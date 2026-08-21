from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?im)^([^\r\n=]*(?:token|secret|password|api[_-]?key)[^\r\n=]*=)\s*[^\r\n]+$"),
)


def redact_secrets(text: str, known_secrets: tuple[str, ...] = ()) -> str:
    redacted = text
    for secret in known_secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def is_authorized(update: dict, allowed_user_ids: frozenset[int]) -> bool:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return False
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    return chat.get("type") == "private" and sender.get("id") in allowed_user_ids


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return ["Codex завершил задачу без текстового отчёта."]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
