"""Compare two or more OpenAI-compatible models on supplier search snapshots.

This command performs live LLM calls and therefore requires ``--allow-live-llm``.
It never runs from the default pytest suite and never refreshes web pages.

Example (from backend/):

    python scripts/run_supplier_model_eval.py ^
      --allow-live-llm --config scripts/supplier_model_eval_configs.example.json ^
      --baseline current --candidate alternative --repeat 3 --output report.json

Exit codes: 0 - promotion gate passed; 1 - quality/configuration failure;
2 - safety regression (promotion must be blocked).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.supplier_model import (
    OpenAICompatibleInvoker,
    SupplierModelEvalError,
    load_configurations,
    load_dataset,
    run_comparative_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-llm", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--full-report",
        action="store_true",
        help="Сохранить/показать разбор каждого кейса и повторного прогона.",
    )
    args = parser.parse_args()
    if not args.allow_live_llm:
        parser.error("live LLM eval требует явного флага --allow-live-llm")

    try:
        dataset = load_dataset(args.dataset_version)
        configurations = load_configurations(args.config)
        invoker = OpenAICompatibleInvoker(
            prompt_version=dataset["prompt_version"]
        )
        report = run_comparative_eval(
            dataset_version=args.dataset_version,
            configurations=configurations,
            repeat_count=args.repeat,
            invoke=invoker,
            baseline_name=args.baseline,
            candidate_name=args.candidate,
            include_case_details=args.full_report,
        )
    except (OSError, json.JSONDecodeError, SupplierModelEvalError) as exc:
        print(f"Eval не запущен: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт сохранён: {args.output}")
    else:
        print(rendered)

    gate = report["promotion_gate"]
    if gate["safety_regressions"]:
        print(
            "БЛОКИРОВКА ПРОДВИЖЕНИЯ: " + ", ".join(gate["safety_regressions"]),
            file=sys.stderr,
        )
        return 2
    if not gate["allowed"]:
        print(
            "Модель не прошла quality gate: "
            + ", ".join(gate["quality_regressions"]),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
