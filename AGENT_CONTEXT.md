# OpsGraph Agent Context

- Date: 2026-03-17
- Product: incident response, recommendation, communication, and retrospective multi-agent system

## Completed Design Layers

- `PRD.md`
- `ARCHITECTURE.md`
- `DATABASE.md`
- `API.md`
- `WORKFLOW.md`
- `PROMPT_TOOL.md`

## Current Implementation State

- Thin product adapters now exist under `src/opsgraph_app/`
- `bootstrap.py` filters shared workflows down to OpsGraph-only entries
- `service.py` exposes domain-facing OpsGraph commands over the shared workflow API
- `routes.py` exposes product-specific FastAPI route definitions
- `repository.py` now uses a SQLAlchemy-backed incident/fact/hypothesis/recommendation/comms/replay repository
- `bootstrap.py` defaults to the SQLAlchemy repository over the shared runtime engine/session
- `sample_payloads.py` includes demo payload and request helpers
- `app.py` exposes a FastAPI factory over the shared workflow API
- Implemented product APIs now include facts, hypothesis decisions, recommendation decisions, severity override, comms publish, resolve/close, postmortem lookup, replay submission, and replay status progression
- Recommendation approval now bridges through a persisted approval-task row linked to recommendation state
- Recommendation execution now enforces tighter terminal-state and approval-task conflict rules
- Comms publish now rejects stale fact-set drafts and requires the bound approved approval task when present
- Resolve/close transitions now enforce root-cause fact presence and resolved-before-close invariants
- Replay runs now execute both incident-backed and replay-case-backed requests through the shared workflow replay path
- Retrospective completion now persists a replay-case snapshot tied back to the postmortem row
- `routes.py` now contains explicit domain-error-to-HTTP mapping logic for product APIs
- Replay runs can now seed file-backed replay fixtures under `replay_fixtures/`, execute the shared `opsgraph_incident_response` workflow through `ReplayFixtureLoader`, and persist workflow run linkage/current state back to the replay row
- Replay baseline capture and evaluation reporting are now implemented end-to-end, with baseline/replay report persistence, node-level diffs, latency deltas, and JSON/Markdown artifacts under `replay_reports/`
- `scripts/run_replay_report.py` now runs local baseline capture -> replay -> compare and emits artifact paths in the returned report payload
- Shared runtime foundation lives in `D:\project\SharedAgentCore`
- Future OpsGraph code should consume vendored shared assets instead of re-implementing registries and runtime helpers

## Intended Repo Layout

- `src/`: future OpsGraph backend/app code
- `tests/`: future product-specific tests
- `scripts/`: helper scripts such as shared-core vendoring
- `shared_core/`: vendored copy of `SharedAgentCore` when this becomes a standalone repo

## First Implementation Targets

1. Expand replay evaluation from report generation into richer comparison metrics and artifact export coverage
2. Add recommendation approval execution and comms orchestration beyond current state mutation flow
3. Add broader failure-path coverage for replay execution and approval orchestration
4. Add replay-case listing/read APIs and richer postmortem-to-replay management

## Local Note

The local workspace source of truth for shared assets remains `D:\project\SharedAgentCore`.
