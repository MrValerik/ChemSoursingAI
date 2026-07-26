"""Настройки (раздел 14 UI/UX-плана): статус каналов. Только администратор."""

from fastapi import APIRouter, Depends

from app.api.deps import require_roles
from app.connectors.email import EmailConnector
from app.core.config import get_settings
from app.extraction.llm_client import LLMClient
from app.models.enums import UserRole

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


@router.get("/channels")
def channels_status() -> list[dict]:
    """Показывает конфигурацию без возврата паролей и токенов."""
    s = get_settings()
    email = EmailConnector(s)
    email_configured = email.imap_configured and email.smtp_configured
    whatsapp_configured = bool(s.whatsapp_token and s.whatsapp_phone_id)
    llm_available, llm_error = LLMClient().check_health()
    return [
        {
            "channel": "email",
            "title": "Email (IMAP/SMTP)",
            "configured": email_configured,
            "status": (
                "подключён для реальной отправки"
                if email_configured
                and s.email_delivery_mode.strip().lower() == "live"
                else "настроен, но внешняя отправка работает в demo"
                if email_configured
                else "не настроен — заполните EMAIL_FROM, IMAP_* и SMTP_*"
            ),
            "details": {
                "imap_host": s.imap_host or None,
                "smtp_host": s.smtp_host or None,
                "delivery_mode": s.email_delivery_mode,
                "auto_followup_mode": s.auto_followup_mode,
            },
        },
        {
            "channel": "whatsapp",
            "title": "WhatsApp Cloud API",
            "configured": whatsapp_configured,
            "status": "настроен (соединение не проверялось)"
            if whatsapp_configured
            else "не настроен — заполните WHATSAPP_* в .env",
            "details": {
                "phone_id": s.whatsapp_phone_id or None,
            },
        },
        {
            "channel": "llm",
            "title": "LLM-инференс (извлечение)",
            "configured": llm_available,
            "status": (
                f"доступен · модель: {s.llm_model}"
                if llm_available
                else "недоступен — проверьте qwen.service и LLM_BASE_URL"
            ),
            "details": {
                "base_url": s.llm_base_url,
                "error": llm_error,
            },
        },
    ]
