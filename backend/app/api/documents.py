"""Документы поставщика: список, исходный файл, текст и проверка агентом."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import Communication, RFQ, SupplierDocument, User
from app.models.enums import Channel, UserRole
from app.schemas.document import (
    ManufacturerDecisionRequest,
    DocumentVerificationRequest,
    SupplierDocumentDetail,
    SupplierDocumentRead,
)
from app.services.document_agent import verify_document
from app.services.document_verification import (
    MANUFACTURER_DECISIONS,
    apply_manufacturer_decision,
)
from app.services.search_trace import utc_now
from app.services.document_storage import read_document_bytes
from app.services.document_text import apply_extraction

router = APIRouter(tags=["documents"])
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}


def _require_rfq_access(user: User, rfq: RFQ | None) -> RFQ:
    if rfq is None or rfq.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    if user.role not in _SEE_ALL_ROLES and rfq.owner_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


def _load_document(
    db: Session, document_id: int, user: User
) -> SupplierDocument:
    document = db.get(SupplierDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document.rfq_id is not None:
        _require_rfq_access(user, db.get(RFQ, document.rfq_id))
    elif user.role not in _SEE_ALL_ROLES:
        communication = (
            db.get(Communication, document.communication_id)
            if document.communication_id is not None
            else None
        )
        if communication is None or communication.channel != Channel.EMAIL:
            raise HTTPException(status_code=404, detail="Документ не найден")
    return document


def _read(document: SupplierDocument, schema=SupplierDocumentRead):
    """Документ вместе с решением человека, наложенным на автосверку."""
    data = schema.model_validate(document)
    data.manufacturer_decided_by_name = (
        document.manufacturer_decided_by.full_name
        if document.manufacturer_decided_by
        else None
    )
    data.verification = apply_manufacturer_decision(
        document.verification,
        decision=document.manufacturer_decision,
        reason=document.manufacturer_decision_reason,
        decided_by=data.manufacturer_decided_by_name,
        decided_at=(
            document.manufacturer_decided_at.isoformat()
            if document.manufacturer_decided_at
            else None
        ),
    )
    return data

@router.get("/rfq/{rfq_id}/documents", response_model=list[SupplierDocumentRead])
def list_rfq_documents(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SupplierDocument]:
    _require_rfq_access(user, db.get(RFQ, rfq_id))
    return [
        _read(document)
        for document in db.scalars(
            select(SupplierDocument)
            .where(SupplierDocument.rfq_id == rfq_id)
            .order_by(SupplierDocument.id.desc())
        ).all()
    ]


@router.get("/documents/{document_id}", response_model=SupplierDocumentDetail)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _read(_load_document(db, document_id, user), SupplierDocumentDetail)


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    document = _load_document(db, document_id, user)
    try:
        payload = read_document_bytes(document)
    except OSError as exc:
        raise HTTPException(
            status_code=410,
            detail="Файл документа больше недоступен в хранилище",
        ) from exc
    # Документ приходит извне, поэтому браузер не должен его исполнять:
    # только скачивание, без inline-просмотра.
    safe_name = document.filename.replace('"', "")
    encoded_name = quote(safe_name, safe="")
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                'attachment; filename="document"; '
                f"filename*=UTF-8''{encoded_name}"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/documents/{document_id}/extract",
    response_model=SupplierDocumentDetail,
)
def extract_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierDocument:
    """Повторно извлекает текст: полезно после установки OCR."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    document = _load_document(db, document_id, user)
    apply_extraction(document)
    db.commit()
    db.refresh(document)
    return document


@router.post(
    "/documents/{document_id}/verify",
    response_model=SupplierDocumentDetail,
)
def verify_supplier_document(
    document_id: int,
    data: DocumentVerificationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SupplierDocument:
    """Проверяет паспорт качества независимым агентом."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    document = _load_document(db, document_id, user)
    rfq = db.get(RFQ, document.rfq_id) if document.rfq_id else None
    verify_document(
        db,
        document,
        expected_cas=data.cas or (rfq.cas if rfq else None),
        expected_name=data.name or (rfq.name if rfq else None),
    )
    db.commit()
    db.refresh(document)
    # Решение человека переживает перепроверку: оно хранится на документе,
    # а не внутри verification, который здесь пересобран заново.
    return _read(document, SupplierDocumentDetail)


@router.post(
    "/documents/{document_id}/manufacturer-decision",
    response_model=SupplierDocumentDetail,
)
def decide_manufacturer(
    document_id: int,
    data: ManufacturerDecisionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Записывает решение человека о том, тот ли изготовитель в паспорте.

    Автоматическая сверка сравнивает имена и на сокращённом названии
    честно отвечает «нужна ручная проверка»: различить «Hefei TNJ Chemical
    Industry» и «TNJ Chemical» по строке нельзя. Без записи решения
    закупщик разбирается заново при каждом открытии документа.

    Решение перекрывает автоматический вывод, но не стирает его: аудиту
    нужно и то, что решил человек, и то, по какому поводу.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    if data.status not in MANUFACTURER_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Допустимые решения: "
                + ", ".join(MANUFACTURER_DECISIONS)
            ),
        )
    document = _load_document(db, document_id, user)
    document.manufacturer_decision = data.status
    document.manufacturer_decision_reason = data.reason.strip()
    document.manufacturer_decided_by_id = user.id
    document.manufacturer_decided_at = utc_now()
    db.commit()
    db.refresh(document)
    return _read(document, SupplierDocumentDetail)


@router.delete(
    "/documents/{document_id}/manufacturer-decision",
    response_model=SupplierDocumentDetail,
)
def clear_manufacturer_decision(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Снимает решение, возвращая действующим автоматический вывод."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    document = _load_document(db, document_id, user)
    document.manufacturer_decision = None
    document.manufacturer_decision_reason = None
    document.manufacturer_decided_by_id = None
    document.manufacturer_decided_at = None
    db.commit()
    db.refresh(document)
    return _read(document, SupplierDocumentDetail)
