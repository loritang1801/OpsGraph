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
- Current product API covers alert intake with Prometheus/Grafana aliases plus accepted-signal/workflow-run ingest metadata and product outbox events for signal/incident updates, persisted webhook idempotency for alert intake plus fact/severity/hypothesis/comms/resolve/close/replay-run mutations, shared envelopes and cursor metadata across incident/replay read routes plus health/workflow endpoints, `/api/v1/events/stream` with workspace/incident topic filtering, filtered incident list/workspace queries with contract-aligned incident/signal/fact aliases, persisted signals plus embedded approval tasks, fact mutation, recommendation/comms decisions including approval-linked generated comms drafts, incident-scoped approval-task read APIs, filtered comms draft queries, resolve/close, replay for incidents and replay cases, replay-case read/list APIs, replay-run filtering by replay case and status, replay-report filtering by replay case, richer replay evaluation metrics, stable replay evaluation/status error codes, and workflow-backed incident response plus retrospective flows with persisted replay-case snapshots and stored postmortem artifact payloads
- Run the local workflow smoke script:

```powershell
python .\scripts\run_demo_workflow.py
```
