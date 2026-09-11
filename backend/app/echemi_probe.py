"""Explicit operator-only server probe, recorded in the normal Echemi history."""
import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import User
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole
from app.connectors.echemi import get_search_progress
from app.echemi_worker import save_progress


def run(query, author_id, attempts):
    query = query.strip()
    if (not query or len(query) > 200 or type(attempts) is not int or attempts not in range(1, 4)):
        raise ValueError("Invalid probe parameters")
    with SessionLocal() as db:
        user = db.get(User, author_id)
        if not user or not user.is_active or user.role != UserRole.ADMIN:
            raise ValueError("An active administrator must own the probe")
        if db.scalar(select(EchemiSearch.id).where(EchemiSearch.status.in_(["queued", "running"])).limit(1)):
            raise ValueError("Wait until the Echemi queue is empty")
        row = EchemiSearch(query=query, author_id=author_id, status="running",
                           message="Тест CAPTCHA: собираем параметры текущей проверки.",
                           diagnostics={"experiment": "alibaba_context_v1", "attempt_limit": attempts})
        db.add(row)
        db.commit()
        search_id = row.id
    print(f"Echemi history ID: {search_id}", flush=True)

    def search():
        with httpx.Client(timeout=930, trust_env=False) as client:
            response = client.post(get_settings().echemi_browser_url.rstrip("/") + "/search",
                                   json={"search_id": search_id, "query": query,
                                         "captcha_probe_attempts": attempts})
            response.raise_for_status()
            payload = response.json()
            if (not isinstance(payload, dict) or payload.get("status") not in
                    {"completed", "partial", "blocked", "failed"}
                    or not isinstance(payload.get("results"), list)
                    or not isinstance(payload.get("diagnostics"), dict)):
                raise ValueError("Invalid browser response")
            return payload

    error = None
    payload = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(search)
            while True:
                try:
                    payload = future.result(timeout=3)
                    break
                except FutureTimeout:
                    if future.done():
                        payload = future.result()
                        break
                    try:
                        snapshot = get_search_progress(search_id)
                        if snapshot:
                            save_progress(search_id, snapshot)
                    except (httpx.HTTPError, ValueError):
                        pass
    except Exception as exc:
        error = type(exc).__name__
    with SessionLocal() as db:
        row = db.get(EchemiSearch, search_id)
        if row.status != "running":
            return search_id  # A worker restart/operator has already finalized this job.
        if payload:
            row.status = payload["status"]
            row.results = [{**item, "detail_status": "not_read"}
                           if item.get("detail_status") in {"pending", "reading"} else item
                           for item in payload["results"]]
            row.message = payload.get("message")
            row.diagnostics = payload["diagnostics"]
        else:
            row.status = "partial" if row.results else "failed"
            row.message = "Тест CAPTCHA прерван из-за ошибки браузерного сервиса."
        row.diagnostics = {**(row.diagnostics or {}), "experiment": "alibaba_context_v1",
                           "attempt_limit": attempts, **({"probe_error_type": error} if error else {})}
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        print(f"Status: {row.status}; results: {len(row.results or [])}", flush=True)
        print(row.diagnostics, flush=True)  # Only the browser's allowlisted diagnostics.
    return search_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--author-id", required=True, type=int)
    parser.add_argument("--attempts", default=2, type=int, choices=[1, 2, 3])
    args = parser.parse_args()
    run(args.query, args.author_id, args.attempts)
