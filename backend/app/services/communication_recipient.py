"""Private recipient identity helpers for matching external replies."""

from __future__ import annotations

import hashlib
import hmac
import re

from app.core.config import get_settings
from app.services.integration_settings import decrypt_secret, encrypt_secret


def normalize_whatsapp_number(value: str) -> str:
    return re.sub(r"\D", "", value)


def recipient_key(channel: str, recipient: str) -> str:
    normalized = (
        normalize_whatsapp_number(recipient)
        if channel == "whatsapp"
        else recipient.strip().casefold()
    )
    settings = get_settings()
    secret = settings.integration_encryption_key or settings.auth_secret_key
    return hmac.new(
        secret.encode("utf-8"),
        f"{channel}:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def protect_recipient(recipient: str) -> str:
    return encrypt_secret(recipient.strip())


def reveal_recipient(ciphertext: str) -> str:
    return decrypt_secret(ciphertext)
