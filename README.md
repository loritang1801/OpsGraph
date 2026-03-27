# OpsGraph

`OpsGraph` is the product workspace for the incident response multi-agent system.

## Current State

This folder now contains the product specs plus a working product-layer implementation under `src/opsgraph_app/`.

Available documents:

- `PRD.md`
- `ARCHITECTURE.md`
- `DATABASE.md`
- `API.md`
- `WORKFLOW.md`
- `PROMPT_TOOL.md`
- `INTEGRATIONS.md`

## Shared Code Strategy

Shared runtime code is maintained centrally in `D:\project\SharedAgentCore`.

Before splitting this project into its own GitHub repository, vendor `SharedAgentCore` into this repo as `shared_core/` by running:

```powershell
.\scripts\vendor_shared_core.ps1
```

## Validation

After vendoring shared code, run tests from `shared_core/`.

## Local Demo

Product-specific thin adapters now live under `src/opsgraph_app/`.

- Build a product-scoped API service from `opsgraph_app.bootstrap`
- Build a domain-facing application service from `opsgraph_app.bootstrap.build_app_service`
- Use `opsgraph_app.app:create_app` as a FastAPI factory when `fastapi` is installed
- Default product repository is now SQLAlchemy-backed and shares the same runtime engine/session as the workflow layer
- Current product API covers alert intake with Prometheus/Grafana aliases plus accepted-signal/workflow-run ingest metadata and product outbox events for signal/incident updates, persisted webhook idempotency for alert intake plus fact/severity/hypothesis/comms/resolve/close/replay-run mutations, local SQLAlchemy-backed auth/session issuance with bearer access tokens plus refresh-token cookies, session-backed `/api/v1/auth/memberships` admin APIs for listing/provisioning/updating org members with immediate session revocation on role or status change, hybrid session-token or header-based viewer/operator/product-admin role gating on read/write routes, shared envelopes and cursor metadata across incident/replay read routes plus health/workflow endpoints, product-admin runtime capability introspection at `/api/v1/opsgraph/runtime-capabilities`, `/health` runtime summaries that now include the last persisted replay-worker heartbeat when available, and runtime-capabilities payloads that include a recent replay-worker heartbeat window, `/api/v1/events/stream` with workspace/incident topic filtering, filtered incident list/workspace queries with contract-aligned incident/signal/fact aliases, persisted signals plus embedded approval tasks, actor-aware timeline entries for manual and replay-admin actions, incident-scoped audit-log queries with actor/session/request metadata for both operator and replay workflows, fact mutation, incident/hypothesis/comms update events for operator actions, approval-requested and approval-updated outbox coverage for generated/manual recommendation approval flow, direct approval-task decision orchestration with optional recommendation execution and linked comms publish, workflow completion events for incident response and retrospective postmortem readiness, recommendation/comms decisions including approval-linked generated comms drafts, incident-scoped approval-task read APIs, filtered comms draft queries, workspace-scoped postmortem listing plus postmortem finalization, resolve/close, replay for incidents and replay cases, replay-case read/list APIs, replay-run filtering by replay case and status, replay-report filtering by replay case, richer replay evaluation metrics, product-admin gating for replay trigger/process-queued/execute/evaluate routes, stable replay evaluation/status error codes, and workflow-backed incident response plus retrospective flows with persisted replay-case snapshots, stored postmortem artifact payloads, and workflow state synchronized to generated incident artifacts
- `deployment.lookup`, `service_registry.lookup`, and `runbook.search` now support env-configured remote HTTP providers with local heuristic fallback. Configure `OPSGRAPH_DEPLOYMENT_LOOKUP_PROVIDER`, `OPSGRAPH_SERVICE_REGISTRY_PROVIDER`, or `OPSGRAPH_RUNBOOK_SEARCH_PROVIDER` as `auto`, `local`, or `http`, then set the matching `..._URL_TEMPLATE`; optional `..._AUTH_TOKEN`, `..._HEADERS_JSON`, `..._CONNECTION_ID`, `..._BACKEND_ID`, and `..._TIMEOUT_SECONDS` envs follow the same pattern.
- Canonical remote-provider request/response contracts are documented in `INTEGRATIONS.md` and backed by fixtures under `tests/fixtures/remote_provider_contracts/`.
- JSON Schema files for those contracts are generated under `schemas/remote_provider_contracts/` via `python .\scripts\generate_remote_provider_schemas.py`.
- `.github/workflows/opsgraph-ci.yml` is generated from the shared `shared_core.agent_platform.product_ci` template; regenerate or drift-check it with `python .\scripts\render_ci_workflow.py` or `python .\scripts\render_ci_workflow.py --check`.
- Run the same validation bundle used by that generated workflow with `python .\scripts\run_ci_checks.py`.
- Demo auth users are seeded automatically when you build the app service:
  - `viewer@example.com` / `opsgraph-demo`
  - `operator@example.com` / `opsgraph-demo`
  - `admin@example.com` / `opsgraph-demo`
- Session auth endpoints:
  - `POST /api/v1/auth/session`
  - `POST /api/v1/auth/session/refresh`
  - `DELETE /api/v1/auth/session/current`
  - `GET /api/v1/me` or `GET /api/v1/auth/me`
  - `GET /api/v1/auth/memberships`
  - `POST /api/v1/auth/memberships`
  - `PATCH /api/v1/auth/memberships/{membership_id}`
- Install optional API and route-test dependencies:

```powershell
python -m pip install -e .[api]
```
- Run the local workflow smoke script:

```powershell
python .\scripts\run_demo_workflow.py
```

- Run the replay regression report flow to emit JSON/Markdown/CSV artifacts under `replay_reports/`:

```powershell
python .\scripts\run_replay_report.py
```

- Process queued replay runs once, with optional seeding:

```powershell
python .\scripts\run_replay_worker.py --seed-run
```

- Run the replay worker in polling mode until the queue goes idle:

```powershell
python .\scripts\run_replay_worker.py --poll --iterations 5 --max-idle-polls 2
```

- Run the replay worker under a supervisor loop with retry/backoff heartbeats:

```powershell
python .\scripts\run_replay_worker.py --supervise --iterations 5 --max-idle-polls 2
```

- For a long-running worker, remove the iteration cap and idle stop:

```powershell
python .\scripts\run_replay_worker.py --supervise --forever --max-idle-polls 0
```

- Replay-worker alert escalation defaults to `warning` at 1 consecutive failure and `critical` at 3 consecutive failures.
- Override those thresholds with `OPSGRAPH_REPLAY_ALERT_WARNING_CONSECUTIVE_FAILURES` and `OPSGRAPH_REPLAY_ALERT_CRITICAL_CONSECUTIVE_FAILURES`.
- `/api/v1/opsgraph/runtime-capabilities` now reports both `replay_worker_alert` and `replay_worker_alert_policy`; `/health` reports `replay_worker_alert_level` once a heartbeat exists.
- Use `GET/PATCH /api/v1/opsgraph/replays/worker-alert-policy?workspace_id=...` for workspace-specific overrides. Sending the default threshold pair resets that workspace back to the runtime default policy.
- Use `GET/PUT/DELETE /api/v1/opsgraph/replays/worker-monitor-shift-schedule?workspace_id=...` to manage the workspace shift table used by replay-worker monitor auto-resolution. Schedules store an IANA timezone, named base time windows such as `day 08:00-20:00` and `night 20:00-08:00`, plus optional exact-date overrides and date-range overrides for holidays or temporary coverage periods. Resolution order is exact date override first, then date-range override, then the base schedule. The same routes now back the in-page `Shift Schedule` editor on `/opsgraph/replays/worker-monitor`, including structured quick-add/remove controls, in-draft up/down reordering, row-to-form edit actions, copy/export/import of standalone schedule JSON, import preview before draft replacement, and a detailed per-window diff that shows added/removed/reordered entries before apply.
- Use `GET /api/v1/opsgraph/replays/worker-monitor-resolved-shift?workspace_id=...` to resolve the currently active shift label from that schedule before applying shift-specific monitor defaults. When either override layer matches, the response identifies that override and does not fall back to the lower-priority schedule for unmatched hours on the same local date.
- Use `GET /api/v1/opsgraph/replays/worker-monitor-presets?workspace_id=...` plus `PUT/DELETE /api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}?workspace_id=...` for shared workspace monitor presets. Add optional `shift_label=...` to the `GET` route when you want `is_default/default_source` resolved against a shift-specific default layer.
- Use `GET /api/v1/opsgraph/replays/worker-monitor-default-preset?workspace_id=...` plus `PUT /api/v1/opsgraph/replays/worker-monitor-default-preset/{preset_name}?workspace_id=...` and `DELETE /api/v1/opsgraph/replays/worker-monitor-default-preset?workspace_id=...` to manage the default monitor view. Add optional `shift_label=...` to read, set, or clear one shift-specific default layer; reads fall back to the workspace default when no shift override exists, while clears only remove the targeted layer.
- Replay-worker policy edits are now written to `GET /api/v1/opsgraph/replays/audit-logs?workspace_id=...`; that route now supports quick filtering by `actor_user_id` and `request_id` so replay admin changes share the same audit trail surface as other product-admin replay actions.

- Product-admin replay routes now include:
  - `POST /api/v1/opsgraph/replays/run`
  - `POST /api/v1/opsgraph/replays/process-queued`
  - `POST /api/v1/opsgraph/replays/{replay_run_id}/execute`
  - `GET /api/v1/opsgraph/replays/worker-alert-policy`
  - `PATCH /api/v1/opsgraph/replays/worker-alert-policy`
  - `GET /api/v1/opsgraph/replays/worker-monitor-shift-schedule`
  - `PUT /api/v1/opsgraph/replays/worker-monitor-shift-schedule`
  - `DELETE /api/v1/opsgraph/replays/worker-monitor-shift-schedule`
  - `GET /api/v1/opsgraph/replays/worker-monitor-resolved-shift`
  - `GET /api/v1/opsgraph/replays/worker-monitor-presets`
  - `PUT /api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}`
  - `DELETE /api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}`
  - `GET /api/v1/opsgraph/replays/worker-monitor-default-preset`
  - `PUT /api/v1/opsgraph/replays/worker-monitor-default-preset/{preset_name}`
  - `DELETE /api/v1/opsgraph/replays/worker-monitor-default-preset`
  - `GET /api/v1/opsgraph/replays/audit-logs`
  - `GET /api/v1/opsgraph/replays/worker-status`
  - `GET /api/v1/opsgraph/replays/worker-status/stream`
- `GET /opsgraph/replays/worker-monitor` with live alert banner, latest-failure panel, recent policy-change log, actor/request quick filters, preset scope switching between workspace-shared presets and browser-local presets, named preset save/load/delete, shift-aware default preset controls with automatic first-load default application in workspace scope when no explicit filter override is present, `Shift Source` manual/auto selection, automatic current-shift resolution from the workspace shift table before applying shift-specific defaults, exact-date and date-range override aware shift status messaging, optional manual `Shift Label` override, inline `Shift Schedule` load/save/clear editing for timezone, base windows, exact-date overrides, and range overrides, structured quick-add/remove controls for the same shift table, in-draft up/down reordering and row-to-form edit actions for fast correction, direct copy/export/import for standalone shift schedule JSON, import preview before applying imported JSON to the current draft, a detailed per-window diff for added/removed/reordered base/date/range entries, copy-request/copy-filter-link/copy-latest-context/row-context actions with `Plain`/`Markdown`/`Slack` formatting, whole-window/latest-row/per-row JSON/CSV export with optional monitor summary metadata including the active copy format, preset scope, shift source, resolved shift label, resolved override date/range/note, preset name, and default source, alert/status-aware filenames, and embedded monitor return links, inline request/result payload expansion, fresh-row highlighting for newly recorded policy edits, adjustable audit row window, older/newest paging controls, and in-page workspace policy editor
