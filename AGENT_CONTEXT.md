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
- Incident list queries now support `status`, `severity`, and `service_id` filters at the product layer
- Incident, signal, fact, recommendation, and comms resource models now serialize with contract-aligned id/status/service/channel aliases, and incident reads now expose `acknowledged_at`
- Recommendation approval now bridges through a persisted approval-task row linked to recommendation state
- Approval tasks can now be listed per incident and fetched directly for operator workbench/read-side integrations
- Incident workspace reads now include persisted signals and approval tasks alongside recommendations, comms drafts, and timeline data
- Recommendation execution now enforces tighter terminal-state and approval-task conflict rules
- Workflow-generated comms drafts now inherit the generated recommendation approval task, and comms publish rejects stale fact-set drafts and requires the bound approved approval task when present
- Comms draft listing now supports `channel` and `status` filters and exposes approval-task linkage plus created timestamps
- Resolve/close transitions now enforce root-cause fact presence and resolved-before-close invariants
- Replay runs now execute both incident-backed and replay-case-backed requests through the shared workflow replay path
- Retrospective completion now persists a replay-case snapshot tied back to the postmortem row and writes a stored postmortem artifact payload
- Replay cases can now be listed and fetched directly from product APIs for postmortem-to-replay navigation
- Replay run listing can now also be filtered by `replay_case_id` and `status` for postmortem-specific replay tracking
- Replay evaluation reports can now also be filtered by `replay_case_id` for postmortem-specific comparison views
- Replay evaluation now raises stable domain codes for not-executed runs and unavailable runtime dependencies
- Incident execution seeds now include persisted signal ids and summaries instead of empty signal placeholders
- Alert and replay submission routes now return `202 Accepted`, `routes.py` contains explicit domain-error-to-HTTP mapping logic for product APIs, and a Grafana webhook alias now lands on the same ingest flow
- Alert ingest responses now also surface accepted-signal counts plus synthetic workflow-run linkage for the queued enrichment path
- Alert ingest now supports persisted webhook idempotency keys, and incident/replay/report list routes now expose shared envelope metadata with cursor pagination
- Fact add/retract, severity override, hypothesis decision, comms publish, resolve/close, and replay-run submission now also support persisted idempotency keys, and the remaining incident/replay read routes now emit shared envelopes instead of bare payloads
- Shared health/workflow endpoints now also emit shared envelopes, `/api/v1/events/stream` now supports workspace/incident topic aliases with payload-backed event context fallback, and replay status updates now reject terminal-state regressions with `REPLAY_STATUS_CONFLICT`
- Replay runs can now seed file-backed replay fixtures under `replay_fixtures/`, execute the shared `opsgraph_incident_response` workflow through `ReplayFixtureLoader`, and persist workflow run linkage/current state back to the replay row
- Replay baseline capture and evaluation reporting are now implemented end-to-end, with baseline/replay report persistence, node-level diffs, derived mismatch metrics, latency deltas, and JSON/Markdown artifacts under `replay_reports/`
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
2. Add recommendation approval execution and comms orchestration beyond current state mutation flow and approval-task read APIs
3. Expand failure-path coverage beyond the current replay evaluation/status domain codes and approval orchestration gaps
4. Add richer postmortem-to-replay management beyond current replay-case read/list, replay-run filtering, and replay-report filtering coverage

## Local Note

The local workspace source of truth for shared assets remains `D:\project\SharedAgentCore`.
