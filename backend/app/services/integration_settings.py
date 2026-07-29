"""Шифрование и разрешение настроек внешних каналов."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import IntegrationSetting


class IntegrationSettingsError(RuntimeError):
    """Сохранённые настройки повреждены или не могут быть расшифрованы."""


_EMAIL_FIELDS = {
    "email_delivery_mode",
    "email_from",
    "email_from_name",
    "email_timeout_s",
    "auto_followup_mode",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_use_ssl",
    "smtp_starttls",
    "imap_host",
    "imap_port",
    "imap_user",
    "imap_password",
    "imap_use_ssl",
    "imap_folder",
}
_WHATSAPP_FIELDS = {
    "whatsapp_token",
    "whatsapp_phone_id",
    "whatsapp_api_base_url",
    "whatsapp_api_version",
    "whatsapp_timeout_s",
}


def _fernet(settings: Settings | None = None) -> Fernet:
    source = settings or get_settings()
    secret = source.integration_encryption_key or source.auth_secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def _decrypt(value: str) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(value.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise IntegrationSettingsError(
            "Сохранённые настройки канала не удалось расшифровать"
        ) from exc
    if not isinstance(payload, dict):
        raise IntegrationSettingsError("Некорректный формат настроек канала")
    return payload


def get_saved_setting(
    db: Session, channel: str
) -> tuple[IntegrationSetting | None, dict[str, Any]]:
    row = db.scalar(
        select(IntegrationSetting).where(IntegrationSetting.channel == channel)
    )
    return row, _decrypt(row.encrypted_config) if row is not None else {}


def save_setting(
    db: Session,
    *,
    channel: str,
    enabled: bool,
    payload: dict[str, Any],
    actor_id: int,
) -> IntegrationSetting:
    row, _ = get_saved_setting(db, channel)
    if row is None:
        row = IntegrationSetting(
            channel=channel,
            enabled=enabled,
            encrypted_config=_encrypt(payload),
            updated_by_id=actor_id,
        )
        db.add(row)
    else:
        row.enabled = enabled
        row.encrypted_config = _encrypt(payload)
        row.updated_by_id = actor_id
    db.commit()
    db.refresh(row)
    return row


def effective_email_settings(
    db: Session,
) -> tuple[Settings, bool, str]:
    base = get_settings()
    row, saved = get_saved_setting(db, "email")
    updates = {key: value for key, value in saved.items() if key in _EMAIL_FIELDS}
    effective = base.model_copy(update=updates)
    enabled = (
        row.enabled
        if row is not None
        else bool(
            effective.email_delivery_mode == "live"
            and effective.smtp_host
            and effective.smtp_password
        )
    )
    return effective, enabled, "database" if row is not None else "environment"


def effective_whatsapp_settings(
    db: Session,
) -> tuple[Settings, bool, str]:
    base = get_settings()
    row, saved = get_saved_setting(db, "whatsapp")
    updates = {
        key: value for key, value in saved.items() if key in _WHATSAPP_FIELDS
    }
    effective = base.model_copy(update=updates)
    enabled = (
        row.enabled
        if row is not None
        else bool(effective.whatsapp_token and effective.whatsapp_phone_id)
    )
    return effective, enabled, "database" if row is not None else "environment"


def mask_recipient(channel: str, recipient: str) -> str:
    value = recipient.strip()
    if channel == "email" and "@" in value:
        local, domain = value.rsplit("@", 1)
        visible = local[:2]
        return f"{visible}{'*' * max(2, len(local) - len(visible))}@{domain}"
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"
