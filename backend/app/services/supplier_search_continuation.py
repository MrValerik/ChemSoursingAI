"""Continuation helpers for repeated supplier searches."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import SearchRun
from app.services.search_countries import normalize_search_country


def supplier_domain(url: object) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    hostname = (urlparse(url).hostname or "").strip().casefold()
    return hostname.removeprefix("www.") or None


def supplier_name_key(name: object) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    normalized = re.sub(r"[^\w]+", " ", name.casefold(), flags=re.UNICODE)
    return " ".join(normalized.split()) or None


def run_country(search_run: SearchRun) -> str | None:
    country = (search_run.input_payload or {}).get("country")
    if not isinstance(country, str) or not country.strip():
        return None
    try:
        return normalize_search_country(country)
    except ValueError:
        return country.strip()


def candidate_results(search_run: SearchRun) -> list[dict]:
    persisted = (search_run.result_payload or {}).get("results")
    if isinstance(persisted, list):
        return [item for item in persisted if isinstance(item, dict)]
    for stage in reversed(search_run.agent_runs):
        if stage.agent_slug != "web_search":
            continue
        legacy = (stage.output_payload or {}).get("results")
        if isinstance(legacy, list):
            return [item for item in legacy if isinstance(item, dict)]
    return []


def qualified_results(search_run: SearchRun) -> list[dict]:
    for stage in reversed(search_run.agent_runs):
        if stage.agent_slug != "supplier_qualification":
            continue
        results = (stage.output_payload or {}).get("qualified_results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
    return []


def country_runs(
    db: Session,
    *,
    rfq_id: int,
    country: str,
    exclude_run_id: int | None = None,
) -> list[SearchRun]:
    normalized_country = normalize_search_country(country)
    runs = list(
        db.scalars(
            select(SearchRun)
            .where(SearchRun.rfq_id == rfq_id)
            .options(
                selectinload(SearchRun.agent_runs),
                selectinload(SearchRun.search_attempts),
                selectinload(SearchRun.source_documents),
                selectinload(SearchRun.evidence_claims),
            )
            .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
        ).all()
    )
    return [
        run
        for run in runs
        if run.id != exclude_run_id and run_country(run) == normalized_country
    ]


def supplier_exclusions(
    runs: Iterable[SearchRun],
) -> tuple[list[str], list[str]]:
    domains: set[str] = set()
    names: set[str] = set()
    for run in runs:
        for candidate in candidate_results(run):
            domain = supplier_domain(candidate.get("url"))
            if domain:
                domains.add(domain)
        for result in qualified_results(run):
            domain = supplier_domain(result.get("url"))
            name = supplier_name_key(result.get("company_name"))
            if domain:
                domains.add(domain)
            if name:
                names.add(name)
    return sorted(domains), sorted(names)


def result_is_excluded(
    result: dict,
    *,
    domains: Iterable[str],
    names: Iterable[str],
) -> bool:
    domain = supplier_domain(result.get("url"))
    normalized_domains = {item.casefold() for item in domains if item}
    if domain and domain in normalized_domains:
        return True
    title = supplier_name_key(result.get("title"))
    if not title:
        return False
    return any(
        len(name) >= 4 and (title == name or f" {name} " in f" {title} ")
        for name in names
        if name
    )


def merge_unique_results(
    runs: Iterable[SearchRun],
) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    qualified: list[dict] = []
    candidate_keys: set[str] = set()
    qualified_keys: set[str] = set()

    for run in runs:
        for result in candidate_results(run):
            key = supplier_domain(result.get("url")) or str(result.get("url") or "")
            if not key or key in candidate_keys:
                continue
            candidate_keys.add(key)
            candidates.append(result)
        for result in qualified_results(run):
            key = (
                supplier_domain(result.get("url"))
                or supplier_name_key(result.get("company_name"))
                or str(result.get("url") or "")
            )
            if not key or key in qualified_keys:
                continue
            qualified_keys.add(key)
            qualified.append(result)
    return candidates, qualified
