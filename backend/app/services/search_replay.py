"""Deterministic replay of persisted supplier-search decisions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import AgentRun, SearchRun
from app.schemas.supplier_verification import SupplierVerification
from app.services.search_trace import (
    create_search_run,
    finish_agent_run,
    finish_search_run,
    log_agent_event,
    start_agent_run,
)
from app.services.supplier_verification import apply_supplier_verification


class SearchReplayError(ValueError):
    """Raised when a persisted run does not contain replayable artifacts."""


def _last_stage(search_run: SearchRun, slug: str) -> AgentRun | None:
    return next(
        (
            stage
            for stage in reversed(search_run.agent_runs)
            if stage.agent_slug == slug
        ),
        None,
    )


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def replay_supplier_validator(
    db: Session,
    *,
    source_run: SearchRun,
    owner_id: int,
) -> SearchRun:
    """Re-run the saved auditor response through current deterministic gates."""
    qualification_stage = _last_stage(source_run, "supplier_qualification")
    verification_stage = _last_stage(source_run, "supplier_verifier")
    if qualification_stage is None or verification_stage is None:
        raise SearchReplayError(
            "Исходный запуск не содержит завершённую квалификацию и проверку аудитора."
        )

    qualification_policy = qualification_stage.policy_output_payload or {}
    base_results = _dict_items(qualification_policy.get("qualified_results"))
    if not base_results:
        raise SearchReplayError(
            "В исходном запуске нет сохранённых результатов квалификации."
        )

    qualification_validation = (
        qualification_stage.validation_output_payload or {}
    )
    accepted_groups = _dict_items(
        qualification_validation.get("accepted_evidence")
    )
    evidence_by_result: dict[int, list[dict[str, Any]]] = {}
    for group in accepted_groups:
        result_index = group.get("result_index")
        if not isinstance(result_index, int):
            continue
        evidence_by_result[result_index] = _dict_items(group.get("claims"))

    raw_payload = deepcopy(verification_stage.raw_output_payload or {})
    raw_batches = _dict_items(raw_payload.get("model_batches"))
    allowed_indexes = {
        int(result["result_index"])
        for result in base_results
        if isinstance(result.get("result_index"), int)
    }
    parsed_by_result: dict[int, SupplierVerification] = {}
    schema_rejections: list[dict[str, Any]] = []
    for batch in raw_batches:
        for item in _dict_items(batch.get("results")):
            try:
                parsed = SupplierVerification.model_validate(item)
            except ValidationError as exc:
                schema_rejections.append(
                    {
                        "result_index": item.get("result_index"),
                        "rejection_reason": str(exc)[:1200],
                    }
                )
                continue
            if parsed.result_index not in allowed_indexes:
                schema_rejections.append(
                    {
                        "result_index": parsed.result_index,
                        "rejection_reason": (
                            "result_index отсутствует среди сохранённых "
                            "квалифицированных кандидатов"
                        ),
                    }
                )
                continue
            parsed_by_result.setdefault(parsed.result_index, parsed)

    final_results = [
        apply_supplier_verification(
            deepcopy(result),
            parsed_by_result.get(int(result["result_index"])),
            evidence_by_result.get(int(result["result_index"]), []),
            unavailable_reason=(
                "Сохранённый ответ аудитора отсутствует или не прошёл "
                "текущий typed-контракт."
            ),
        )
        for result in base_results
        if isinstance(result.get("result_index"), int)
    ]
    claim_reference_validation = [
        {
            "result_index": result["result_index"],
            "status": (result.get("verification") or {}).get("status"),
            "supporting_claim_ids": (
                result.get("verification") or {}
            ).get("supporting_claim_ids", []),
            "contradictory_claim_ids": (
                result.get("verification") or {}
            ).get("contradictory_claim_ids", []),
            "invalid_claim_ids": (
                result.get("verification") or {}
            ).get("invalid_claim_ids", []),
        }
        for result in final_results
    ]

    replay_input = {
        **deepcopy(source_run.input_payload or {}),
        "replay": {
            "mode": "validator",
            "parent_search_run_id": source_run.id,
            "parent_correlation_id": source_run.correlation_id,
            "scope": "supplier_verifier",
        },
    }
    replay_run = create_search_run(
        db,
        owner_id=owner_id,
        rfq_id=source_run.rfq_id,
        input_payload=replay_input,
        mode="validator_replay",
        graph_version=source_run.graph_version,
    )
    replay_run.parent_search_run_id = source_run.id
    replay_run.replay_mode = "validator"
    replay_run.result_payload = deepcopy(source_run.result_payload)

    stage, stage_clock = start_agent_run(
        db,
        search_run=replay_run,
        sequence=1,
        agent_slug="supplier_verifier",
        agent_name="Replay независимой проверки поставщиков",
        execution_type="validator_replay",
        input_payload={
            "parent_search_run_id": source_run.id,
            "parent_agent_run_id": verification_stage.id,
            "qualified_results": deepcopy(base_results),
            "accepted_evidence": deepcopy(accepted_groups),
        },
        contract_version=verification_stage.contract_version,
    )
    log_agent_event(
        stage,
        "Replay: применяю текущий typed-контракт и veto gate к сохранённому "
        f"ответу аудитора запуска №{source_run.id}",
    )
    log_agent_event(
        stage,
        f"Пересчитано кандидатов: {len(final_results)}, "
        f"в коротком списке: "
        f"{sum(bool(result.get('shortlist_eligible')) for result in final_results)}, "
        f"отклонено схемой: {len(schema_rejections)}",
    )
    finish_agent_run(
        stage,
        stage_clock,
        output_payload={
            "qualified_results": final_results,
            "replay_of_agent_run_id": verification_stage.id,
        },
        raw_output_payload=raw_payload,
        parsed_output_payload={
            "results": [
                parsed.model_dump()
                for _, parsed in sorted(parsed_by_result.items())
            ]
        },
        validation_output_payload={
            "schema_rejections": schema_rejections,
            "claim_reference_validation": claim_reference_validation,
        },
        policy_output_payload={
            "qualified_results": final_results,
            "shortlist_count": sum(
                bool(result.get("shortlist_eligible"))
                for result in final_results
            ),
        },
    )
    finish_search_run(replay_run)
    return replay_run
