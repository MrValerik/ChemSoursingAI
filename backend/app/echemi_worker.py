"""Single database-backed consumer. Browser execution is isolated from DB credentials."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from sqlalchemy import select, text, update
from app.core.db import SessionLocal, engine
from app.models.echemi_search import EchemiSearch
from app.connectors.echemi import EchemiBrowserBusy, search_echemi, get_search_progress
from app.core.config import get_settings


def save_progress(search_id, payload):
    with SessionLocal() as db:
        row = db.get(EchemiSearch, search_id)
        if row is None or row.status != "running":
            return
        row.results = payload["results"]
        row.diagnostics = payload["diagnostics"]
        if payload.get("message"):
            row.message = payload["message"]
        db.commit()


def collect_with_progress(query, search_id):
    # Keep the long POST connection alive while polling snapshots independently.
    interval = get_settings().echemi_progress_poll_seconds
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(search_echemi, query, search_id)
        while True:
            try:
                return pending.result(timeout=interval)
            except FutureTimeout:
                if pending.done():
                    return pending.result()  # A TimeoutError raised by the job itself.
            try:
                payload = get_search_progress(search_id)
                if payload is not None:
                    save_progress(search_id, payload)
            except Exception as exc:
                # A missed snapshot must not abort the active search.
                logging.warning("Echemi progress %s unavailable: %s", search_id, type(exc).__name__)


def run_one():
    with SessionLocal() as db:
        row = db.scalar(select(EchemiSearch).where(EchemiSearch.status == "queued")
                        .order_by(EchemiSearch.id).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        row.status = "running"
        row.message = "Открываем Echemi и читаем карточки. Это может занять несколько минут."
        search_id, query = row.id, row.query
        db.commit()
    try:
        payload = collect_with_progress(query, search_id)
    except EchemiBrowserBusy:
        with SessionLocal() as db:
            row = db.get(EchemiSearch, search_id)
            attempts = (row.diagnostics or {}).get("busy_retries", 0) + 1
            exhausted = attempts >= get_settings().echemi_busy_retries
            row.status = "failed" if exhausted else "queued"
            row.message = ("Браузер занят предыдущим поиском и не освободился вовремя. Повторите запрос позже."
                           if exhausted else "Ожидаем освобождения браузера. Поиск начнётся автоматически.")
            row.diagnostics = {"busy_retries": attempts, "http_status": 409}
            row.finished_at = datetime.now(timezone.utc) if exhausted else None
            db.commit()
        # Back off through the normal idle sleep, rather than retrying in a tight loop.
        return False
    except Exception as exc:
        logging.warning("Echemi search %s failed: %s", search_id, type(exc).__name__)
        payload = {"status": "failed", "results": [],
                   "message": "Браузерный сервис недоступен или превысил время ожидания.",
                   "diagnostics": {"error_type": type(exc).__name__}}
    with SessionLocal() as db:
        row = db.get(EchemiSearch, search_id)
        if row.results and not payload["results"] and payload["status"] in {"failed", "blocked"}:
            payload = {**payload, "status": "partial", "results": row.results,
                       "message": (payload.get("message") or "Поиск прерван.") + " Ранее найденные данные сохранены.",
                       "diagnostics": {**(row.diagnostics or {}), **payload.get("diagnostics", {})}}
        row.status = payload["status"]
        row.results = [
            {**item, "detail_status": "not_read"} if item.get("detail_status") in {"pending", "reading"} else item
            for item in payload["results"]
        ]
        row.message = payload.get("message")
        row.diagnostics = payload.get("diagnostics", {})
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
    return True


def main():
    # A session lock also prevents multiple replicas from using the shared browser profile.
    with engine.connect() as lock:
        if engine.dialect.name == "postgresql":
            acquired = lock.scalar(text("SELECT pg_try_advisory_lock(701092026)"))
            lock.commit()
            if not acquired:
                raise RuntimeError("Echemi worker already running")
        with SessionLocal() as db:
            db.execute(update(EchemiSearch).where(EchemiSearch.status == "running").values(
                status="failed", message="Поиск прерван перезапуском сервиса. Создайте новый запрос.",
                finished_at=datetime.now(timezone.utc)))
            db.commit()
        while True:
            if not run_one():
                time.sleep(3)


if __name__ == "__main__":
    main()
