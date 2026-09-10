"""Single database-backed consumer. Browser execution is isolated from DB credentials."""
import logging
import time
from datetime import datetime, timezone
from sqlalchemy import select, text, update
from app.core.db import SessionLocal, engine
from app.models.echemi_search import EchemiSearch
from app.connectors.echemi import search_echemi


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
        payload = search_echemi(query)
    except Exception as exc:
        logging.warning("Echemi search %s failed: %s", search_id, type(exc).__name__)
        payload = {"status": "failed", "results": [],
                   "message": "Браузерный сервис недоступен или превысил время ожидания.",
                   "diagnostics": {"error_type": type(exc).__name__}}
    with SessionLocal() as db:
        row = db.get(EchemiSearch, search_id)
        row.status = payload["status"]
        row.results = payload["results"]
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
