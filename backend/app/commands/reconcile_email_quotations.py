"""Сверяет сохранённые Email-котировки после миграции связи с письмами."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.core.db import SessionLocal
from app.services.quotation_reconciliation import reconcile_email_quotations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rfq-id", type=int, default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--verify-documents", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = reconcile_email_quotations(
            db,
            rfq_id=args.rfq_id,
            email_address=args.email,
            verify_documents=args.verify_documents,
        )
    print(json.dumps(asdict(result), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
