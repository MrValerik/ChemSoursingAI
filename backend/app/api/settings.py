"""Администрирование каналов: безопасные настройки и проверка соединений."""

from __future__ import annotations

from email.utils import parseaddr

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.connectors.email import (
    EmailConfigurationError,
    EmailConnector,
    EmailDeliveryError,
)
from app.connectors.whatsapp import (
    WhatsAppConfigurationError,
    WhatsAppConnector,
    WhatsAppDeliveryError,
)
from app.core.db import get_db
from app.extraction.llm_client import LLMClient
from app.models import User
from app.models.enums import UserRole
from app.schemas.integration import (
    EmailIntegrationRead,
    EmailIntegrationUpdate,
    IntegrationConnectionRead,
    WhatsAppIntegrationRead,
    WhatsAppIntegrationUpdate,
    WhatsAppWebQrRead,
    WhatsAppWebPairingCodeCreate,
    WhatsAppWebPairingCodeRead,
    WhatsAppWebStatusRead,
)
from app.services.integration_settings import (
    effective_email_settings,
    effective_whatsapp_settings,
    save_setting,
)

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


def _email_read(db: Session) -> EmailIntegrationRead:
    s, enabled, source = effective_email_settings(db)
    configured = EmailConnector(s).smtp_configured and EmailConnector(
        s
    ).imap_configured
    return EmailIntegrationRead(
        enabled=enabled,
        configured=configured,
        source=source,
        delivery_mode=s.email_delivery_mode,
        email_from=s.email_from,
        email_from_name=s.email_from_name,
        email_timeout_s=s.email_timeout_s,
        auto_followup_mode=s.auto_followup_mode,
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_user=s.smtp_user,
        smtp_password_set=bool(s.smtp_password),
        smtp_use_ssl=s.smtp_use_ssl,
        smtp_starttls=s.smtp_starttls,
        imap_host=s.imap_host,
        imap_port=s.imap_port,
        imap_user=s.imap_user,
        imap_password_set=bool(s.imap_password),
        imap_use_ssl=s.imap_use_ssl,
        imap_folder=s.imap_folder,
    )


def _whatsapp_read(db: Session) -> WhatsAppIntegrationRead:
    s, enabled, source = effective_whatsapp_settings(db)
    return WhatsAppIntegrationRead(
        enabled=enabled,
        configured=WhatsAppConnector(s).configured,
        source=source,
        transport=s.whatsapp_transport,
        web_gateway_available=bool(s.whatsapp_web_service_token),
        phone_id=s.whatsapp_phone_id,
        token_set=bool(s.whatsapp_token),
        api_base_url=s.whatsapp_api_base_url,
        api_version=s.whatsapp_api_version,
        timeout_s=s.whatsapp_timeout_s,
    )


@router.get("/channels")
def channels_status(db: Session = Depends(get_db)) -> list[dict]:
    """Показывает состояние без возврата паролей и токенов."""
    email = _email_read(db)
    whatsapp = _whatsapp_read(db)
    llm_available, llm_error = LLMClient().check_health()
    return [
        {
            "channel": "email",
            "title": "Email (IMAP/SMTP)",
            "configured": email.configured,
            "status": (
                "включён для реальной отправки"
                if email.configured
                and email.enabled
                and email.delivery_mode == "live"
                else "настроен; внешняя отправка отключена"
                if email.configured
                else "не настроен"
            ),
            "details": {
                "imap_host": email.imap_host or None,
                "smtp_host": email.smtp_host or None,
                "delivery_mode": email.delivery_mode,
                "source": email.source,
            },
        },
        {
            "channel": "whatsapp",
            "title": (
                "WhatsApp Web" if whatsapp.transport == "web" else "WhatsApp Cloud API"
            ),
            "configured": whatsapp.configured,
            "status": (
                "включён"
                if whatsapp.configured and whatsapp.enabled
                else "настроен; отправка отключена"
                if whatsapp.configured
                else "не настроен"
            ),
            "details": {
                "phone_id": whatsapp.phone_id or None,
                "api_version": whatsapp.api_version,
                "transport": whatsapp.transport,
                "source": whatsapp.source,
            },
        },
        {
            "channel": "llm",
            "title": "Локальная LLM",
            "configured": llm_available,
            "status": (
                "доступна"
                if llm_available
                else "недоступна — проверьте qwen.service и LLM_BASE_URL"
            ),
            "details": {"error": llm_error},
        },
    ]


@router.get("/integrations/email", response_model=EmailIntegrationRead)
def get_email_integration(db: Session = Depends(get_db)) -> EmailIntegrationRead:
    return _email_read(db)


@router.put("/integrations/email", response_model=EmailIntegrationRead)
def update_email_integration(
    payload: EmailIntegrationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> EmailIntegrationRead:
    current, _, _ = effective_email_settings(db)
    smtp_password = (
        ""
        if payload.clear_secrets
        else payload.smtp_password
        if payload.smtp_password is not None
        else current.smtp_password
    )
    imap_password = (
        ""
        if payload.clear_secrets
        else payload.imap_password
        if payload.imap_password is not None
        else current.imap_password
    )
    config = {
        "email_delivery_mode": payload.delivery_mode,
        "email_from": payload.email_from,
        "email_from_name": payload.email_from_name,
        "email_timeout_s": payload.email_timeout_s,
        "auto_followup_mode": payload.auto_followup_mode,
        "smtp_host": payload.smtp_host,
        "smtp_port": payload.smtp_port,
        "smtp_user": payload.smtp_user,
        "smtp_password": smtp_password,
        "smtp_use_ssl": payload.smtp_use_ssl,
        "smtp_starttls": payload.smtp_starttls,
        "imap_host": payload.imap_host,
        "imap_port": payload.imap_port,
        "imap_user": payload.imap_user,
        "imap_password": imap_password,
        "imap_use_ssl": payload.imap_use_ssl,
        "imap_folder": payload.imap_folder,
    }
    candidate = current.model_copy(update=config)
    configured = EmailConnector(candidate).smtp_configured and EmailConnector(
        candidate
    ).imap_configured
    if payload.enabled and not configured:
        raise HTTPException(
            status_code=422,
            detail="Для включения Email заполните адрес отправителя, SMTP, IMAP и пароли",
        )
    sender = parseaddr(candidate.email_from)[1]
    if payload.enabled and (
        not sender or sender.casefold() != candidate.email_from.casefold()
    ):
        raise HTTPException(
            status_code=422,
            detail="Укажите корректный адрес отправителя Email",
        )
    if payload.smtp_use_ssl and payload.smtp_starttls:
        raise HTTPException(
            status_code=422,
            detail="SMTP SSL и STARTTLS нельзя включать одновременно",
        )
    save_setting(
        db,
        channel="email",
        enabled=payload.enabled,
        payload=config,
        actor_id=user.id,
    )
    return _email_read(db)


@router.post(
    "/integrations/email/check", response_model=IntegrationConnectionRead
)
def check_email_integration(
    db: Session = Depends(get_db),
) -> IntegrationConnectionRead:
    settings, _, _ = effective_email_settings(db)
    try:
        details = EmailConnector(settings).check_connections()
    except (EmailConfigurationError, EmailDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return IntegrationConnectionRead(
        channel="email",
        ok=True,
        message="SMTP и IMAP подтвердили подключение",
        details=details,
    )


@router.get("/integrations/whatsapp", response_model=WhatsAppIntegrationRead)
def get_whatsapp_integration(
    db: Session = Depends(get_db),
) -> WhatsAppIntegrationRead:
    return _whatsapp_read(db)


@router.put(
    "/integrations/whatsapp", response_model=WhatsAppIntegrationRead
)
def update_whatsapp_integration(
    payload: WhatsAppIntegrationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> WhatsAppIntegrationRead:
    current, _, _ = effective_whatsapp_settings(db)
    token = (
        ""
        if payload.clear_token
        else payload.access_token
        if payload.access_token is not None
        else current.whatsapp_token
    )
    config = {
        "whatsapp_transport": payload.transport,
        "whatsapp_token": token,
        "whatsapp_phone_id": payload.phone_id,
        "whatsapp_api_base_url": payload.api_base_url,
        "whatsapp_api_version": payload.api_version,
        "whatsapp_timeout_s": payload.timeout_s,
    }
    if payload.enabled and not WhatsAppConnector(
        current.model_copy(update=config)
    ).configured:
        raise HTTPException(
            status_code=422,
            detail=(
                "WhatsApp Web gateway не настроен на сервере"
                if payload.transport == "web"
                else "Для включения WhatsApp укажите токен и Phone Number ID"
            ),
        )
    save_setting(
        db,
        channel="whatsapp",
        enabled=payload.enabled,
        payload=config,
        actor_id=user.id,
    )
    return _whatsapp_read(db)


@router.post(
    "/integrations/whatsapp/check", response_model=IntegrationConnectionRead
)
def check_whatsapp_integration(
    db: Session = Depends(get_db),
) -> IntegrationConnectionRead:
    settings, _, _ = effective_whatsapp_settings(db)
    try:
        details = WhatsAppConnector(settings).check_health()
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return IntegrationConnectionRead(
        channel="whatsapp",
        ok=True,
        message=(
            "WhatsApp Web gateway доступен"
            if settings.whatsapp_transport == "web"
            else "WhatsApp Cloud API подтвердил Phone Number ID"
        ),
        details=details,
    )


def _web_connector(db: Session) -> WhatsAppConnector:
    settings, _, _ = effective_whatsapp_settings(db)
    if settings.whatsapp_transport != "web":
        settings = settings.model_copy(update={"whatsapp_transport": "web"})
    connector = WhatsAppConnector(settings)
    if not connector.configured:
        raise HTTPException(
            status_code=503, detail="WhatsApp Web gateway не настроен на сервере"
        )
    return connector


@router.get(
    "/integrations/whatsapp/web/status", response_model=WhatsAppWebStatusRead
)
def whatsapp_web_status(db: Session = Depends(get_db)) -> dict:
    try:
        return _web_connector(db).web_status()
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/integrations/whatsapp/web/connect", response_model=WhatsAppWebStatusRead
)
def whatsapp_web_connect(db: Session = Depends(get_db)) -> dict:
    try:
        return _web_connector(db).web_connect()
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/integrations/whatsapp/web/qr", response_model=WhatsAppWebQrRead)
def whatsapp_web_qr(
    response: Response, db: Session = Depends(get_db)
) -> WhatsAppWebQrRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        return WhatsAppWebQrRead(qr_data_url=_web_connector(db).web_qr())
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/integrations/whatsapp/web/pairing-code",
    response_model=WhatsAppWebPairingCodeRead,
)
def whatsapp_web_pairing_code(
    payload: WhatsAppWebPairingCodeCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _web_connector(db).web_pairing_code(payload.phone_number)
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/integrations/whatsapp/web/pairing-code/cancel",
    response_model=WhatsAppWebStatusRead,
)
def whatsapp_web_cancel_pairing_code(db: Session = Depends(get_db)) -> dict:
    try:
        return _web_connector(db).web_cancel_pairing_code()
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/integrations/whatsapp/web/disconnect", response_model=WhatsAppWebStatusRead
)
def whatsapp_web_disconnect(db: Session = Depends(get_db)) -> dict:
    try:
        return _web_connector(db).web_disconnect()
    except (WhatsAppConfigurationError, WhatsAppDeliveryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
