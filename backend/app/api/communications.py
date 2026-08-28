"""История общения по RFQ и безопасная синхронизация входящей почты."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.connectors.email import EmailConfigurationError, EmailDeliveryError
from app.connectors.google_translate import GoogleTranslateError
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Communication, RFQ, User
from app.models.enums import Channel, UserRole
from app.schemas.communication import (
    CommunicationDraftSend,
    CommunicationMessageRead,
    CommunicationOverviewRead,
    CommunicationSendCreate,
    CommunicationTranslationCreate,
    CommunicationTranslationRead,
    EmailSyncRead,
)
from app.services.communication_delivery import (
    CommunicationSendError,
    send_conversation_message,
    send_email_draft,
)
from app.services.communication_history import list_communication_overview
from app.services.communication_translation import translate_communication_messages
from app.services.email_workflow import sync_inbox

router = APIRouter(tags=["communications"])

_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _visible_rfq(db: Session, rfq_id: int, user: User) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    if (
        user.role not in _SEE_ALL_ROLES
        and rfq.owner_id is not None
        and rfq.owner_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


@router.get(
    "/rfq/{rfq_id}/communications",
    response_model=CommunicationOverviewRead,
)
def communication_overview(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationOverviewRead:
    _visible_rfq(db, rfq_id, user)
    return list_communication_overview(db, rfq_id)


@router.post(
    "/rfq/{rfq_id}/communications/translation",
    response_model=CommunicationTranslationRead,
)
def translate_communications(
    rfq_id: int,
    payload: CommunicationTranslationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationTranslationRead:
    """Переводит выбранную переписку для интерфейса, не меняя оригиналы."""
    _visible_rfq(db, rfq_id, user)
    try:
        translations = translate_communication_messages(
            db,
            rfq_id=rfq_id,
            message_ids=payload.message_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GoogleTranslateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CommunicationTranslationRead(translations=translations)


def _message_read(message: Communication) -> CommunicationMessageRead:
    return CommunicationMessageRead(
        id=message.id,
        direction=message.direction,
        channel=message.channel,
        subject=message.subject,
        body=message.body,
        status=message.status,
        from_address=message.from_address,
        to_address=message.to_address,
        attachments=message.attachments,
        created_at=message.created_at,
    )


def _ensure_can_send(user: User) -> None:
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")


@router.post(
    "/rfq/{rfq_id}/communications/send",
    response_model=CommunicationMessageRead,
    status_code=201,
)
def send_message(
    rfq_id: int,
    payload: CommunicationSendCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationMessageRead:
    """Явно отправляет сообщение в уже начатый Email/WhatsApp-диалог."""
    rfq = _visible_rfq(db, rfq_id, user)
    _ensure_can_send(user)
    try:
        message = send_conversation_message(
            db,
            rfq=rfq,
            manager_id=payload.manager_id,
            channel=payload.channel,
            body=payload.body,
            subject=payload.subject,
            idempotency_key=str(payload.idempotency_key),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationSendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _message_read(message)


@router.post(
    "/rfq/{rfq_id}/communications/send-with-attachments",
    response_model=CommunicationMessageRead,
    status_code=201,
)
async def send_message_with_attachments(
    rfq_id: int,
    manager_id: int = Form(gt=0),
    channel: Channel = Form(),
    body: str = Form(default="", max_length=12_000),
    subject: str | None = Form(default=None, max_length=998),
    idempotency_key: UUID = Form(),
    confirm_external_send: bool = Form(default=False),
    files: list[UploadFile] = File(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationMessageRead:
    """Отправляет файлы вместе с сообщением в существующий диалог."""
    rfq = _visible_rfq(db, rfq_id, user)
    _ensure_can_send(user)
    if not confirm_external_send:
        raise HTTPException(
            status_code=422, detail="Подтвердите реальную внешнюю отправку"
        )
    if not 1 <= len(files) <= 5:
        raise HTTPException(
            status_code=422, detail="За одно сообщение можно отправить от 1 до 5 файлов"
        )
    max_bytes = get_settings().attachment_max_size_mb * 1024 * 1024
    total_limit = max_bytes * 2
    total_bytes = 0
    attachments: list[dict] = []
    for upload in files:
        try:
            content = await upload.read(max_bytes + 1)
        finally:
            await upload.close()
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Файл {upload.filename or 'document'} больше "
                    f"{get_settings().attachment_max_size_mb} МБ"
                ),
            )
        total_bytes += len(content)
        if total_bytes > total_limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Общий размер файлов превышает допустимый лимит "
                    f"{get_settings().attachment_max_size_mb * 2} МБ"
                ),
            )
        attachments.append(
            {
                "filename": upload.filename or "document",
                "content_type": upload.content_type or "application/octet-stream",
                "content": content,
            }
        )
    try:
        message = send_conversation_message(
            db,
            rfq=rfq,
            manager_id=manager_id,
            channel=channel,
            body=body,
            subject=subject,
            idempotency_key=str(idempotency_key),
            attachments=attachments,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationSendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _message_read(message)


@router.post(
    "/communications/{communication_id}/send",
    response_model=CommunicationMessageRead,
)
def send_draft(
    communication_id: int,
    payload: CommunicationDraftSend,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommunicationMessageRead:
    """Явно отправляет сохранённый Email-дозапрос из истории общения."""
    _ensure_can_send(user)
    communication = db.get(Communication, communication_id)
    if communication is None or communication.rfq_id is None:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    _visible_rfq(db, communication.rfq_id, user)
    try:
        sent = send_email_draft(db, communication=communication)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommunicationSendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _message_read(sent)


@router.post("/communications/email/sync", response_model=EmailSyncRead)
def sync_email(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailSyncRead:
    """Явно проверяет IMAP; автоматические ответы проходят policy-gate."""
    if user.role not in {UserRole.BUYER, UserRole.HEAD, UserRole.ADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Проверка общей почты доступна закупщику, руководителю и администратору",
        )
    try:
        summary = sync_inbox(db, limit=limit)
    except EmailConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EmailSyncRead(**summary.as_dict())
