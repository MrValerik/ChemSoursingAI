---
name: chemsource-backend
description: Implement or review ChemSource AI backend changes in FastAPI, Pydantic, SQLAlchemy, PostgreSQL, connectors, authentication, RFQ workflows, supplier search, quotations, or migrations. Use for files under backend/app, backend/migrations, backend/tests, backend/requirements.txt, or backend/Dockerfile.
---

# ChemSource Backend

Keep the FastAPI backend evidence-first, safe by default, and compatible with Windows development plus Linux containers.

## Prepare

1. Read `AGENTS.md` and the relevant sections of `docs/PRODUCT_REQUIREMENTS.md`, `docs/STRUCTURE.md`, and `docs/RUN.md`.
2. Inspect the existing schema, service, connector, route, and tests before adding an entity or helper.
3. Preserve unrelated working-tree changes.

## Respect boundaries

- Keep API routes in `backend/app/api`, orchestration in `backend/app/services`, persistence in `backend/app/models`, request/response contracts in `backend/app/schemas`, and external integrations in `backend/app/connectors`.
- Keep LLM provider details behind the existing OpenAI-compatible client.
- Put configuration in `Settings`; update `.env.example` for every new environment variable.
- Preserve original external text, URL, language, retrieval time, and machine interpretation as separate data.
- Treat website, email, attachment, and LLM content as untrusted input.
- Keep email delivery in `demo` and follow-ups in `draft` unless the user explicitly authorizes a live external action.

## Implement

1. Extend an existing model, schema, service, or validator when its semantics match.
2. Validate at the API boundary and enforce business invariants in the service layer.
3. Keep external operations idempotent and store their technical identifier and audit outcome.
4. Add paired `.up.sql` and `.down.sql` migrations for schema changes; do not mutate production schema during module import.
5. Keep the rule-based fallback operational when the LLM is unavailable.
6. Return uncertainty, missing evidence, and escalation reasons explicitly.
7. Synchronize changed API contracts with `frontend/src/api/types.ts`, the client, and `docs/RUN.md`.

## Validate on Windows

Run commands from the repository root with PowerShell:

```powershell
Push-Location .\backend
try {
    ..\.venv\Scripts\python.exe -m pytest
} finally {
    Pop-Location
}
```

If `.venv` does not exist, create it only when dependency installation is in scope:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
```

Run focused tests first, then the full backend suite. Add normal, boundary, failure, authorization, and no-LLM cases when changing business behavior.
