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
- Current product API covers alert intake with Prometheus/Grafana aliases plus accepted-signal/workflow-run ingest metadata and product outbox events for signal/incident updates, persisted webhook idempotency for alert intake plus fact/severity/hypothesis/comms/resolve/close/replay-run mutations, local SQLAlchemy-backed auth/session issuance with bearer access tokens plus refresh-token cookies, session-backed `/api/v1/auth/memberships` admin APIs for listing/provisioning/updating org members with immediate session revocation on role or status change, hybrid session-token or header-based viewer/operator/product-admin role gating on read/write routes, shared envelopes and cursor metadata across incident/replay read routes plus health/workflow endpoints, `/api/v1/events/stream` with workspace/incident topic filtering, filtered incident list/workspace queries with contract-aligned incident/signal/fact aliases, persisted signals plus embedded approval tasks, actor-aware timeline entries for manual and replay-admin actions, incident-scoped audit-log queries with actor/session/request metadata for both operator and replay workflows, fact mutation, incident/hypothesis/comms update events for operator actions, approval-requested and approval-updated outbox coverage for generated/manual recommendation approval flow, direct approval-task decision orchestration with optional recommendation execution and linked comms publish, workflow completion events for incident response and retrospective postmortem readiness, recommendation/comms decisions including approval-linked generated comms drafts, incident-scoped approval-task read APIs, filtered comms draft queries, workspace-scoped postmortem listing plus postmortem finalization, resolve/close, replay for incidents and replay cases, replay-case read/list APIs, replay-run filtering by replay case and status, replay-report filtering by replay case, richer replay evaluation metrics, product-admin gating for replay trigger/execute/evaluate routes, stable replay evaluation/status error codes, and workflow-backed incident response plus retrospective flows with persisted replay-case snapshots and stored postmortem artifact payloads
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
