---
name: chemsource-testing
description: Add, select, run, or troubleshoot ChemSource AI tests and validation on Windows, including pytest backend tests, React and TypeScript builds, Docker Compose checks, migrations, connectors, authentication, RFQ workflows, AI extraction, and regression fixtures.
---

# ChemSource Testing

Validate the smallest affected surface first, then expand in proportion to risk.

## Select coverage

- Add normal, boundary, invalid-input, authorization, and failure-path tests for changed backend behavior.
- Add no-LLM and malformed-LLM cases for extraction or search changes.
- Mock SMTP, IMAP, web search, PubChem, and LLM calls unless the task is explicitly an integration test.
- Use synthetic supplier and quotation data. Do not place real correspondence or commercial data in fixtures.
- Assert persisted audit state and idempotency for external-operation workflows.
- Treat a frontend production build as the minimum check until a dedicated frontend test runner exists.

## Run on Windows

Prefer the virtual environment interpreter directly so PowerShell execution policy cannot block activation:

```powershell
Push-Location .\backend
try {
    ..\.venv\Scripts\python.exe -m pytest .\tests\test_pipeline.py -q
    ..\.venv\Scripts\python.exe -m pytest
} finally {
    Pop-Location
}

npm --prefix .\frontend run build
docker compose config
```

Use `py -m pytest` only when dependencies are intentionally installed in that interpreter.

## Diagnose failures

1. Separate product failures from Windows path, encoding, file-locking, line-ending, port, or Docker Desktop issues.
2. Preserve the first relevant traceback and reproduce with the narrowest test.
3. Do not weaken an assertion merely to make the suite pass.
4. Do not call live external systems from the default test suite.
5. Report the exact commands run, results, and anything not verified.
