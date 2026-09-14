"""Личная, отключённая по умолчанию рассылка после квалификации поиска."""

import logging
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Communication, Manager, PurchaseDecision, RFQ, RfqRecipient,
    RfqSupplierLink, SearchRun, Supplier, User,
)
from app.models.enums import Channel, DispatchStatus, RFQStatus, UserRole
from app.services.combined_communication import (
    CombinedDispatchError, CombinedMessage, dispatch_combined_message,
)
from app.services.communication_links import communication_linked_to_rfq
from app.services.rfq_service import (
    ensure_rfq_english, prepare_rfq_english_text, render_rfq_text,
)

logger = logging.getLogger(__name__)


def auto_dispatch_after_search(
    db: Session, *, search_run: SearchRun, results: list[dict], registry_links: list[dict],
) -> None:
    """Ошибки рассылки не превращают успешный поиск в неудачный."""
    run_id = search_run.id
    try:
        _dispatch(db, search_run, results, registry_links)
    except Exception:
        db.rollback()
        logger.warning("Automatic dispatch failed for search %s; see recipient audit", run_id)
        run = db.get(SearchRun, run_id)
        if run is not None:
            run.input_payload = {**(run.input_payload or {}), "auto_dispatch": {
                "status": "error",
                "reason": "Рассылка не завершена. Проверьте получателей и историю общения; автоматического повтора нет.",
            }}
            db.commit()


def _dispatch(db: Session, run: SearchRun, results: list[dict], links: list[dict]) -> None:
    user = db.get(User, run.owner_id)
    if user is not None:
        db.refresh(user)
    if (run.status != "completed" or run.replay_mode
            or (run.input_payload or {}).get("auto_dispatch")
            or not user or not user.is_active or not user.auto_dispatch_after_search
            or user.role == UserRole.AUDITOR or not run.rfq_id):
        return
    rfq = db.get(RFQ, run.rfq_id)
    if rfq is not None:
        db.refresh(rfq)
    if (not rfq or rfq.deleted_at or rfq.identification_method == "analog"
            or rfq.status in {RFQStatus.CLOSED, RFQStatus.ESCALATED}
            or (rfq.owner_id not in (None, user.id)
                and user.role not in {UserRole.ADMIN, UserRole.HEAD})
            or db.scalar(select(PurchaseDecision.id).where(PurchaseDecision.rfq_id == rfq.id))):
        return

    eligible = {item["result_index"] for item in results
                if item.get("shortlist_eligible") is True
                and (item.get("verification") or {}).get("status") == "confirmed"}
    supplier_ids = sorted({item["supplier_id"] for item in links
                           if item["result_index"] in eligible})
    sent = errors = 0
    for supplier_id in supplier_ids:
        supplier = db.get(Supplier, supplier_id)
        link = db.scalar(select(RfqSupplierLink).where(
            RfqSupplierLink.rfq_id == rfq.id, RfqSupplierLink.supplier_id == supplier_id,
        ))
        if not supplier or supplier.qualification_status == "rejected" or not link or link.status == "excluded":
            continue
        # Любой выбранный ранее канал оставляем ручному сценарию; в том числе
        # ошибочную и неопределённую попытку, которую опасно повторять.
        if db.scalar(select(RfqRecipient.id).where(
            RfqRecipient.rfq_id == rfq.id, RfqRecipient.supplier_id == supplier_id,
        )):
            continue
        manager = next((m for m in supplier.managers if m.email
                        and parseaddr(m.email)[1] == m.email and "@" in m.email
                        and not any(c in m.email for c in "\r\n")), None)
        if manager is None:
            continue
        # Не начинаем заново уже существующую переписку, включая другой
        # контакт компании или тот же адрес в дублирующей карточке.
        if db.scalar(select(Communication.id).join(Manager, Communication.manager_id == Manager.id).where(
            communication_linked_to_rfq(rfq.id),
            (Manager.supplier_id == supplier_id)
            | (func.lower(Communication.to_address) == manager.email.lower()),
        ).limit(1)):
            continue
        prepare_rfq_english_text(rfq)
        subject, body = render_rfq_text(rfq)
        subject = f"[RFQ-{rfq.id}] {subject}"
        ensure_rfq_english(subject, body)
        recipient = RfqRecipient(rfq_id=rfq.id, supplier_id=supplier_id,
                                 channel=Channel.EMAIL, status=DispatchStatus.QUEUED)
        db.add(recipient)
        db.flush()
        # Общий сервис сохраняет уникальную попытку до SMTP и историю RFQ.
        try:
            dispatch_combined_message(
                db, prepared=CombinedMessage(supplier, manager, [rfq], [recipient],
                                             Channel.EMAIL, subject, body),
                idempotency_key=f"dispatch-{recipient.id}", confirm_external_send=True,
            )
            recipient.note = "Автоматическая рассылка после поиска" + (
                " (демо)" if recipient.note and "демо" in recipient.note else ""
            )
            db.commit()
            sent += 1
        except CombinedDispatchError:
            errors += 1
    run.input_payload = {**(run.input_payload or {}), "auto_dispatch": {
        "status": "completed", "sent": sent, "errors": errors,
    }}
    db.commit()
