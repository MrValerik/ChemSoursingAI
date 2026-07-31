"""Release-gate CLI for the supplier decision eval.

Usage (from backend/):

    python scripts/run_supplier_eval.py [--version v1] [--full-report]

Exit codes: 0 — all cases pass; 1 — mismatches without a new false accept;
2 — a safety-critical false accept (release must be blocked).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.supplier_decision import run_supplier_decision_eval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v1")
    parser.add_argument(
        "--full-report",
        action="store_true",
        help="Печатать также разбор каждого примера, а не только метрики.",
    )
    args = parser.parse_args()

    report = run_supplier_decision_eval(args.version)
    printable = dict(report)
    if not args.full_report:
        printable.pop("cases", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))

    if report["safety_violations"]:
        print(
            "БЛОКИРОВКА РЕЛИЗА: ложный допуск на safety-примерах: "
            + ", ".join(report["safety_violations"]),
            file=sys.stderr,
        )
        return 2
    if report["failed_case_ids"]:
        print(
            "Есть расхождения с ожидаемыми решениями: "
            + ", ".join(report["failed_case_ids"]),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
