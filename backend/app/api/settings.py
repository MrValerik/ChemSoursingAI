"""Настройки (раздел 14 UI/UX-плана): статус каналов. Только администратор."""

from fastapi import APIRouter, Depends

from app.api.deps import require_roles
from app.core.config import get_settings
from app.models.enums import UserRole

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


@router.get("/channels")
def channels_status() -> list[dict]:
    """Статус подключения каналов: параметры берутся из окружения (.env).

    Проверка соединения (IMAP login / WhatsApp API) появится вместе
    с коннекторами на этапе интеграций.
    """
    s = get_settings()
    email_configured = bool(s.imap_host and s.smtp_host)
    whatsapp_configured = bool(s.whatsapp_token and s.whatsapp_phone_id)
    return [
        {
            "channel": "email",
            "title": "Email (IMAP/SMTP)",
            "configured": email_configured,
            "status": "настроен (соединение не проверялось)"
            if email_configured
            else "не настроен — заполните IMAP_*/SMTP_* в .env",
            "details": {
                "imap_host": s.imap_host or None,
                "smtp_host": s.smtp_host or None,
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
            "configured": True,
            "status": f"эндпоинт: {s.llm_base_url} · модель: {s.llm_model}",
            "details": None,
        },
    ]
