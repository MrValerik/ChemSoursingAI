"""One attempt per RFQ/product. Ambiguous transport failures never retry."""
from datetime import datetime, timezone
from sqlalchemy import select, update
from app.models import EchemiOutreach, RFQ, User
from app.models.enums import UserRole
from app.services.integration_settings import _decrypt, IntegrationSettingsError
from app.connectors.echemi import submit_inquiry


def recover(db):
    db.execute(update(EchemiOutreach).where(EchemiOutreach.status == "sending").values(
        status="unknown", message="Отправка прервана. Проверьте историю Echemi; автоматический повтор отключён.",
        finished_at=datetime.now(timezone.utc)))
    db.commit()


def run_one(sessions):
    with sessions() as db:
        row = db.scalar(select(EchemiOutreach).where(EchemiOutreach.status == "queued")
                        .order_by(EchemiOutreach.id).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        user, rfq = db.get(User, row.author_id), db.get(RFQ, row.rfq_id)
        allowed = (user is not None and user.is_active and user.role in {UserRole.ADMIN, UserRole.BUYER}
                   and rfq is not None and rfq.deleted_at is None
                   and (user.role == UserRole.ADMIN or rfq.owner_id in {None, user.id}))
        if not allowed:
            row.status, row.message = "blocked", "Доступ автора к запросу изменился; отправка отменена."
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            return True
        row.status = "sending"
        job_id, url, encrypted, seller_name = row.id, row.product_url, row.encrypted_payload, row.seller_name
        db.commit()  # Persist BEFORE the network call.
    try:
        payload = _decrypt(encrypted)
    except IntegrationSettingsError:
        result = {"status": "blocked", "message": "Не удалось расшифровать данные отправителя. Проверьте ключ шифрования обработчика Echemi. Форма не отправлена."}
    else:
        payload["seller_name"] = seller_name
        try:
            result = submit_inquiry(job_id, url, payload)
        except Exception:
            result = {"status": "unknown", "message": "Результат не подтверждён. Проверьте историю Echemi; автоматический повтор отключён."}
    with sessions() as db:
        row = db.get(EchemiOutreach, job_id)
        if row is not None:
            row.status = result["status"]
            row.message = result["message"]
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
    return True
