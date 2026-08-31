"""Opt-in, synthetic communication replay; never invokes delivery or saves replies.

Run from backend: python -m app.eval.communication_quality --live --repeat 2
Only LLM inference (including its technical concurrency leases) can write state.
The business DB, mailboxes and communication history are not touched.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace


def load_cases() -> list[dict]:
    path = Path(__file__).with_name("datasets") / "communication_quality.v1.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    assert dataset["dataset_version"] == "v1"
    cases = dataset["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    return cases


def score_reply(case: dict, reply: str) -> list[str]:
    failures = [f"missing: {pattern}" for pattern in case["must_match"] if not re.search(pattern, reply)]
    failures += [f"forbidden: {pattern}" for pattern in case["must_not_match"] if re.search(pattern, reply)]
    if re.search(r"\bwe\s+can\s+work\s+with\s+(?:the\s+)?(?:USD|CNY|EUR|\d|price|payment|terms)", reply, re.I):
        failures.append("unsupported commercial acceptance")
    if len(re.findall(r"\b[\w'-]+\b", reply)) > case.get("max_words", 130) or reply.count("?") > 3:
        failures.append("too long / too many questions")
    return failures


class SnapshotDB:
    """Minimal detached prompt/profile snapshot, deliberately no real DB handle."""
    def __init__(self, prompt):
        self.prompt = SimpleNamespace(system_prompt=prompt)

    def commit(self):
        pass

    def get(self, *args):
        return None

    def scalar(self, statement):
        from app.models import PromptTemplate, CommunicationProfile
        entity = statement.column_descriptions[0]["entity"]
        if entity is PromptTemplate:
            return self.prompt
        if entity is CommunicationProfile:
            return SimpleNamespace(system_instructions="Collect missing commercial facts without approving any order.")
        raise AssertionError(f"Unexpected query in isolated eval: {entity}")


def run_live_case(case: dict, *, prompt: str | None = None) -> dict:
    from app.services.communication_llm import communication_llm_client
    from app.services.communication_testing import _continue_prompt, _generate_reply
    from app.services.supplier_communication_prompts import SUPPLIER_COMMUNICATION_PROMPT

    client = communication_llm_client()
    run = SimpleNamespace(
        rfq_id=None, actor_id=None, reply_language="en", channel="email",
        additional_instructions="", procurement_context=case["context"],
        messages=[SimpleNamespace(sender_role="supplier", content=value) for value in case["supplier"]],
        status="preview", error=None,
    )
    drafts = []
    generate = client.generate_text

    def record(**kwargs):
        draft = generate(**kwargs)
        drafts.append(draft)
        return draft

    client.generate_text = record
    started = time.perf_counter()
    row = {"case": case["id"], "model": client.model, "dataset": "communication_quality.v1"}
    try:
        row["reply"] = _generate_reply(SnapshotDB(prompt or SUPPLIER_COMMUNICATION_PROMPT), run=run,
                                       user_text=_continue_prompt(run), stage="reply", llm=client)
        row["failures"] = score_reply(case, row["reply"])
    except Exception as exc:
        row["error"] = type(exc).__name__
        row["reason"] = run.error
        row["failures"] = ["no usable reply"]
    row.update(drafts=drafts, seconds=round(time.perf_counter() - started, 3))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly permit billable LLM calls with synthetic data")
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    if not args.live:
        parser.error("Live model calls require --live; ordinary pytest stays offline")
    for repeat in range(args.repeat):
        for case in load_cases():
            print(json.dumps({"repeat": repeat + 1, **run_live_case(case)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
