---
name: chemsource-frontend
description: Implement or review ChemSource AI React, Vite, and TypeScript interface changes, including RFQ screens, supplier search, quotations, review queues, settings, authentication, API types, and visible risk or evidence states. Use for files under frontend/src, frontend/package.json, frontend/vite.config.ts, frontend/nginx.conf, or frontend/Dockerfile.
---

# ChemSource Frontend

Build a compact Russian-language workspace that exposes evidence, uncertainty, and the next safe action.

## Prepare

1. Read `AGENTS.md`, `docs/PRODUCT_REQUIREMENTS.md`, and the relevant API schemas.
2. Reuse existing components, labels, styles, request helpers, and types before creating new ones.
3. Identify the user role, business action, loading state, empty state, error state, and restricted state.

## Implement

- Keep `frontend/src/api/types.ts` aligned with backend response schemas.
- Route HTTP access through `frontend/src/api/client.ts`; do not scatter raw fetch logic across components.
- Show source, evidence snippet, confidence, missing fields, risk reason, and escalation state where a decision depends on them.
- Never encode status only through color. Pair color with text and accessible semantics.
- Preserve progressive disclosure: keep the primary workflow concise and place prompts, raw JSON, traces, and supporting evidence in explicit expandable sections.
- Keep destructive or external actions distinguishable from draft and preview actions.
- Preserve Russian UI text and the exact product name `ChemSource AI`.
- Avoid adding a state library or component framework unless current complexity demonstrates a concrete need.
- Keep tokens and credentials out of rendered diagnostics and logs.

## Validate on Windows

Use PowerShell from the repository root:

```powershell
npm --prefix .\frontend ci
npm --prefix .\frontend run build
```

Do not reinstall dependencies when `node_modules` already matches the lockfile and installation is outside the task. Run the build after type or API changes and report any skipped browser-level verification.
