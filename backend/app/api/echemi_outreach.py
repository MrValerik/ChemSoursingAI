"""Confirmed, RFQ-scoped form delivery with durable duplicate protection."""
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models import RFQ, EchemiSearch, EchemiOutreach
from app.models.enums import UserRole
from app.services.echemi_rfq import visible_rfq
from app.services.echemi_sender import read_sender
from app.services.integration_settings import _encrypt, IntegrationSettingsError
from app.services.rfq_service import render_rfq_text

router = APIRouter(prefix="/rfq/{rfq_id}/echemi-outreach", tags=["echemi"])
writer = require_roles(UserRole.ADMIN, UserRole.BUYER)


class DeliveryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_id: int = Field(gt=0)
    product_urls: list[str] = Field(min_length=1, max_length=20)
    message: str = Field(min_length=20, max_length=5000)
    confirmed: bool = Field(strict=True)
    sender_version: str = Field(min_length=64, max_length=64)


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    search_id: int
    product_url: str
    seller_name: str
    status: str
    message: str | None
    created_at: datetime
    finished_at: datetime | None


def target_url(value):
    try:
        p = urlsplit(value)
        if p.scheme == "https" and p.netloc == "www.echemi.com" and re.fullmatch(r"/produce/[\w-]+\.html", p.path):
            return urlunsplit(("https", p.netloc, p.path, "", ""))
    except ValueError:
        pass
    return None


def sender_version(sender):
    return hashlib.sha256(json.dumps(sender.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def require_request(db, user, rfq_id, *, lock=False):
    stmt = visible_rfq(user).where(RFQ.id == rfq_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, "Запрос не найден")
    return row


@router.get("", response_model=list[DeliveryRead])
def history(rfq_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_request(db, user, rfq_id)
    return db.scalars(select(EchemiOutreach).where(EchemiOutreach.rfq_id == rfq_id)
                      .order_by(EchemiOutreach.id)).all()


@router.get("/preview")
def preview(rfq_id: int, response: Response, db: Session = Depends(get_db), user=Depends(writer)):
    response.headers["Cache-Control"] = "no-store"
    rfq = require_request(db, user, rfq_id)
    try:
        sender = read_sender(db, user.id if user.role == UserRole.BUYER else None)
    except (IntegrationSettingsError, ValueError):
        raise HTTPException(503, "Не удалось прочитать данные отправителя.")
    subject, body = render_rfq_text(rfq)
    return {"message": subject + "\n\n" + body, "sender": sender, "sender_version": sender_version(sender)}


@router.post("", response_model=list[DeliveryRead], status_code=202)
def enqueue(rfq_id: int, payload: DeliveryCreate, db: Session = Depends(get_db), user=Depends(writer)):
    rfq = require_request(db, user, rfq_id, lock=True)
    if not payload.confirmed:
        raise HTTPException(422, "Подтвердите адресатов, контакты и текст рассылки.")
    if rfq.identification_method == "analog":
        raise HTTPException(422, "Сначала выберите конкретное вещество.")
    search = db.get(EchemiSearch, payload.search_id)
    if search is None or search.rfq_id != rfq_id:
        raise HTTPException(404, "Поиск не найден")
    if search.status not in {"completed", "partial"}:
        raise HTTPException(409, "Дождитесь завершения поиска.")
    try:
        sender = read_sender(db, user.id if user.role == UserRole.BUYER else None)
    except (IntegrationSettingsError, ValueError):
        raise HTTPException(503, "Не удалось прочитать данные отправителя.")
    if not sender.configured:
        raise HTTPException(422, "Заполните email, компанию, имя, телефон и страну в настройках Echemi.")
    if payload.sender_version != sender_version(sender):
        raise HTTPException(409, "Контакты изменились. Заново подготовьте и проверьте рассылку.")
    if len(payload.message.strip()) < 20:
        raise HTTPException(422, "Текст обращения слишком короткий.")
    targets = {target_url(r.get("product_url", "")): r for r in search.results}
    urls = list(dict.fromkeys(payload.product_urls))
    for url in urls:
        row = targets.get(url)
        if not target_url(url) or row is None or not row.get("seller_name"):
            raise HTTPException(422, "Выберите карточки с указанной компанией из этого поиска.")
        if rfq.cas and row.get("cas_numbers") != [rfq.cas]:
            raise HTTPException(422, "CAS в карточке отсутствует или противоречит запросу.")
    existing = {r.product_url: r for r in db.scalars(select(EchemiOutreach).where(
        EchemiOutreach.rfq_id == rfq_id)).all()}
    rows = []
    for url in urls:
        company = " ".join(targets[url]["seller_name"].casefold().split())
        duplicate = next((r for r in existing.values() if " ".join(r.seller_name.casefold().split()) == company), None)
        if url not in existing and duplicate:
            rows.append(duplicate)
            continue
        if url not in existing:
            row = EchemiOutreach(rfq_id=rfq_id, search_id=search.id, author_id=user.id,
                product_url=url, seller_name=targets[url]["seller_name"][:500],
                encrypted_payload=_encrypt({"sender": sender.model_dump(mode="json"),
                                             "message": payload.message.strip()}))
            db.add(row)
            existing[url] = row
        elif existing[url].status == "blocked":
            # A new explicit confirmation may retry only a known pre-submit block.
            row = existing[url]
            row.attempts = [*(row.attempts or []), {"status": row.status, "message": row.message,
                "author_id": row.author_id, "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "encrypted_payload": row.encrypted_payload}]
            row.status, row.message, row.finished_at = "queued", None, None
            row.author_id = user.id
            row.encrypted_payload = _encrypt({"sender": sender.model_dump(mode="json"), "message": payload.message.strip()})
        rows.append(existing[url])
    db.commit()
    return rows
