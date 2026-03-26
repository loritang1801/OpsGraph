# OpsGraph Agent Context

- Date: 2026-03-26
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
- `auth.py` now provides local SQLAlchemy-backed auth/session storage, seeded demo users, bearer access tokens, refresh-token rotation, and hybrid session/header authorization for `viewer`, `operator`, and `product_admin` routes
- `auth.py` now also provides membership admin APIs for listing/provisioning/updating org memberships, and role/status changes revoke active sessions in that org immediately
- `bootstrap.py` defaults to the SQLAlchemy repository over the shared runtime engine/session
- `sample_payloads.py` includes demo payload and request helpers
- `app.py` exposes a FastAPI factory over the shared workflow API
- Implemented product APIs now include facts, hypothesis decisions, recommendation decisions, severity override, comms publish, resolve/close, postmortem lookup, replay submission, and replay status progression
- Incident list queries now support `status`, `severity`, and `service_id` filters at the product layer
- Incident, signal, fact, recommendation, and comms resource models now serialize with contract-aligned id/status/service/channel aliases, and incident reads now expose `acknowledged_at`
- Recommendation approval now bridges through a persisted approval-task row linked to recommendation state
- Approval tasks can now be listed per incident and fetched directly for operator workbench/read-side integrations
- Incident-scoped audit logs can now be listed directly from product APIs, and both manual incident actions and replay-admin mutations persist actor/session/request metadata
- Recommendation approval decisions now emit `opsgraph.approval.updated`, and incident-response completion emits `opsgraph.approval.requested` when the workflow materializes a new approval task
- Incident workspace reads now include persisted signals and approval tasks alongside recommendations, comms drafts, and timeline data
- Recommendation execution now enforces tighter terminal-state and approval-task conflict rules
- Workflow-generated comms drafts now inherit the generated recommendation approval task, and comms publish rejects stale fact-set drafts and requires the bound approved approval task when present
- Approval tasks can now also be decided directly through a product API that optionally executes the linked recommendation and publishes linked comms drafts in one orchestration step
- Comms draft listing now supports `channel` and `status` filters and exposes approval-task linkage plus created timestamps
- Resolve/close transitions now enforce root-cause fact presence and resolved-before-close invariants
- Replay runs now execute both incident-backed and replay-case-backed requests through the shared workflow replay path
- Retrospective completion now persists a replay-case snapshot tied back to the postmortem row and writes a stored postmortem artifact payload
- Postmortems can now be listed at workspace scope with optional incident/status filters for postmortem-to-replay management views
- Postmortems can now also be finalized through a product mutation that stamps finalization metadata, updates the stored artifact payload, and emits `opsgraph.postmortem.updated`
- Replay cases can now be listed and fetched directly from product APIs for postmortem-to-replay navigation
- Replay run listing can now also be filtered by `replay_case_id` and `status` for postmortem-specific replay tracking
- Replay evaluation reports can now also be filtered by `replay_case_id` for postmortem-specific comparison views
- Replay evaluation now raises stable domain codes for not-executed runs and unavailable runtime dependencies
- Incident execution seeds now include persisted signal ids and summaries instead of empty signal placeholders
- Alert and replay submission routes now return `202 Accepted`, `routes.py` contains explicit domain-error-to-HTTP mapping logic for product APIs, and a Grafana webhook alias now lands on the same ingest flow
- Alert ingest responses now also surface accepted-signal counts plus synthetic workflow-run linkage for the queued enrichment path, and alert ingest now emits `opsgraph.signal.ingested` plus incident created/updated outbox events for the product event stream
- Alert ingest now supports persisted webhook idempotency keys, and incident/replay/report list routes now expose shared envelope metadata with cursor pagination
- Fact add/retract, severity override, hypothesis decision, comms publish, resolve/close, and replay-run submission now also support persisted idempotency keys, and the remaining incident/replay read routes now emit shared envelopes instead of bare payloads
- Incident mutations now also emit product outbox events for incident updates, hypothesis updates, and comms publish so the SSE stream reflects operator actions beyond webhook ingest
- Timeline entries for manual incident actions and replay-admin mutations now also persist actor, subject, and payload metadata instead of only free-text summaries
- Replay trigger, baseline capture, status mutation, execute, and evaluate flows now emit incident-scoped audit rows with `replay.start_run`, `replay.capture_baseline`, `replay.update_status`, `replay.execute`, and `replay.evaluate` action types
- Workflow-backed incident response now emits incident-update events on completion, and retrospective completion emits `opsgraph.postmortem.ready` with the stored postmortem artifact context
- Shared health/workflow endpoints now also emit shared envelopes, `/api/v1/events/stream` now supports workspace/incident topic aliases with payload-backed event context fallback, and replay status updates now reject terminal-state regressions with `REPLAY_STATUS_CONFLICT`
- Replay runs can now seed file-backed replay fixtures under `replay_fixtures/`, execute the shared `opsgraph_incident_response` workflow through `ReplayFixtureLoader`, and persist workflow run linkage/current state back to the replay row
- Replay baseline capture and evaluation reporting are now implemented end-to-end, with baseline/replay report persistence, node-level diffs, richer derived mismatch metrics, latency deltas, and JSON/Markdown/CSV artifacts under `replay_reports/`
- `scripts/run_replay_report.py` now runs local baseline capture -> replay -> compare and emits report summary plus artifact paths in the returned payload
- FastAPI routes now expose `/api/v1/auth/session`, `/api/v1/auth/session/refresh`, `/api/v1/auth/session/current`, and `/api/v1/me` auth/session endpoints when `fastapi` is installed
- FastAPI routes now also expose `/api/v1/auth/memberships` list/provision/update endpoints for `product_admin` membership management
- FastAPI routes now enforce documented read/write role boundaries via session bearer tokens or the existing `Authorization`, `X-Organization-Id`, `X-User-Id`, and `X-User-Role` header contract for local/demo compatibility
- Route integration tests now run against real FastAPI `TestClient` flows instead of staying skipped, including session auth, replay admin, and membership admin paths
- Replay trigger, replay baseline capture, replay status mutation, replay execute, and replay evaluate routes now require `product_admin` or stronger, matching the API contract
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
