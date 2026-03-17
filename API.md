# OpsGraph API and Event Contracts

- Version: v0.1
- Date: 2026-03-16
- Scope: `OpsGraph` REST, webhook, SSE, and async event contracts

## 1. Contract Summary

This document defines the implementation-grade interface for `OpsGraph`:

1. Alert webhook ingestion
2. Incident list and incident workspace APIs
3. Fact, hypothesis, recommendation, and communication APIs
4. Incident resolution and replay APIs
5. OpsGraph-specific SSE and async event contracts

Shared authentication, approval, workflow, artifact, and feedback contracts are defined in the shared platform contract.

## 2. Domain Design Rules

1. Webhook ingestion is idempotent and must tolerate duplicate delivery
2. Facts, hypotheses, and recommendations are separate resource families
3. Communication publish requires fact-set safety checks
4. High-risk recommendation execution always routes through shared `approval_task`
5. Incident workspace reads are optimized for current-state snapshots plus append-only timeline

## 3. Domain Resource Shapes

### 3.1 `IncidentSummary`

```json
{
  "id": "uuid",
  "incident_key": "INC-2026-0001",
  "title": "Elevated 5xx on checkout-api",
  "severity": "sev1",
  "status": "investigating",
  "service_id": "uuid",
  "opened_at": "2026-03-16T09:00:00Z",
  "acknowledged_at": "2026-03-16T09:01:00Z"
}
```

### 3.2 `SignalSummary`

```json
{
  "id": "uuid",
  "source": "prometheus",
  "status": "firing",
  "title": "HighErrorRate",
  "dedupe_key": "checkout-api:high-error-rate",
  "fired_at": "2026-03-16T09:00:00Z"
}
```

### 3.3 `IncidentFact`

```json
{
  "id": "uuid",
  "fact_type": "impact",
  "status": "confirmed",
  "statement": "Checkout requests are failing for 27% of users.",
  "fact_set_version": 3,
  "source_refs": [
    {
      "kind": "signal",
      "id": "uuid"
    }
  ],
  "created_at": "2026-03-16T09:03:00Z"
}
```

### 3.4 `HypothesisSummary`

```json
{
  "id": "uuid",
  "rank": 1,
  "status": "proposed",
  "confidence": 0.78,
  "title": "Recent checkout-api deploy introduced connection pool exhaustion.",
  "evidence_refs": [
    {
      "kind": "deployment",
      "id": "deploy-123"
    }
  ],
  "updated_at": "2026-03-16T09:05:00Z"
}
```

### 3.5 `RecommendationSummary`

```json
{
  "id": "uuid",
  "recommendation_type": "mitigate",
  "risk_level": "high_risk",
  "status": "proposed",
  "requires_approval": true,
  "approval_task_id": "uuid",
  "title": "Roll back checkout-api deployment 123",
  "updated_at": "2026-03-16T09:06:00Z"
}
```

### 3.6 `CommsDraftSummary`

```json
{
  "id": "uuid",
  "channel_type": "internal_slack",
  "status": "draft",
  "fact_set_version": 3,
  "approval_task_id": null,
  "created_at": "2026-03-16T09:08:00Z"
}
```

## 4. Webhook Ingestion API

### 4.1 `POST /api/v1/opsgraph/alerts/prometheus`

Purpose: ingest one raw Prometheus/Alertmanager-derived webhook payload.

Auth: webhook token

Headers:

| Header | Required | Purpose |
| --- | --- | --- |
| `X-Webhook-Token` | Yes | Shared secret configured in connector |
| `X-Request-Id` | Recommended | Trace correlation |
| `Idempotency-Key` | Optional | If upstream can provide stable delivery id |

Request body:

- Raw Prometheus-compatible webhook JSON

Server behavior:

1. Persist raw payload to `signal.raw_payload_json`
2. Normalize payload into one or more `signal` rows
3. Compute `dedupe_key`
4. Correlate to an existing incident or create a new one
5. Enqueue enrichment workflow

Response:

- `202 Accepted`

```json
{
  "data": {
    "accepted_signals": 1,
    "incident_id": "uuid",
    "incident_created": true
  },
  "meta": {
    "workflow_run_id": "uuid"
  }
}
```

Errors:

- `INVALID_WEBHOOK_TOKEN`
- `INVALID_ALERT_PAYLOAD`
- `IDEMPOTENCY_CONFLICT`

### 4.2 `POST /api/v1/opsgraph/alerts/grafana`

Purpose: ingest one raw Grafana webhook payload.

Auth: webhook token

Headers and behavior:

- Same as Prometheus endpoint

Response:

- `202 Accepted`

## 5. Incident REST API

### 5.1 `GET /api/v1/opsgraph/incidents`

Purpose: list incidents for one workspace.

Auth: `viewer`

Query params:

- `workspace_id` required
- `status`
- `severity`
- `service_id`
- `cursor`
- `limit`

Response:

- `200 OK` with paginated `IncidentSummary[]`

### 5.2 `GET /api/v1/opsgraph/incidents/:incidentId`

Purpose: fetch full incident workspace state.

Auth: `viewer`

Response:

- `200 OK`

```json
{
  "data": {
    "incident": {
      "id": "uuid",
      "incident_key": "INC-2026-0001",
      "severity": "sev1",
      "status": "investigating",
      "current_fact_set_version": 3
    },
    "signals": [],
    "facts": [],
    "hypotheses": [],
    "recommendations": [],
    "comms_drafts": [],
    "timeline": []
  }
}
```

### 5.3 `POST /api/v1/opsgraph/incidents/:incidentId/facts`

Purpose: add a confirmed fact to the incident.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "fact_type": "impact",
  "statement": "Checkout requests are failing for 27% of users.",
  "source_refs": [
    {
      "kind": "signal",
      "id": "uuid"
    }
  ],
  "expected_fact_set_version": 2
}
```

Rules:

1. Server increments fact set version on success
2. `source_refs` must reference existing incident-visible objects

Response:

- `200 OK`

```json
{
  "data": {
    "fact": {
      "id": "uuid",
      "fact_set_version": 3
    },
    "current_fact_set_version": 3
  }
}
```

Errors:

- `FACT_VERSION_CONFLICT`
- `INCIDENT_ALREADY_CLOSED`
- `INVALID_SOURCE_REFERENCE`

### 5.4 `POST /api/v1/opsgraph/incidents/:incidentId/facts/:factId/retract`

Purpose: retract a previously confirmed fact.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "reason": "Metric query was corrected.",
  "expected_fact_set_version": 3
}
```

Response:

- `200 OK`

```json
{
  "data": {
    "fact_id": "uuid",
    "status": "retracted",
    "current_fact_set_version": 4
  }
}
```

### 5.5 `POST /api/v1/opsgraph/incidents/:incidentId/severity`

Purpose: override incident severity.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "severity": "sev2",
  "reason": "Impact confirmed lower than initial signal.",
  "expected_updated_at": "2026-03-16T09:05:00Z"
}
```

Response:

- `200 OK` with updated `IncidentSummary`

Errors:

- `INCIDENT_STATUS_CONFLICT`
- `CONFLICT_STALE_RESOURCE`

### 5.6 `POST /api/v1/opsgraph/incidents/:incidentId/hypotheses/:hypothesisId/decision`

Purpose: accept or reject a hypothesis.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "decision": "accept",
  "comment": "Matches deploy timing and database saturation metrics.",
  "expected_updated_at": "2026-03-16T09:06:00Z"
}
```

Allowed decisions:

- `accept`
- `reject`

Response:

- `200 OK`

```json
{
  "data": {
    "hypothesis_id": "uuid",
    "status": "accepted"
  }
}
```

Errors:

- `HYPOTHESIS_STATUS_CONFLICT`
- `CONFLICT_STALE_RESOURCE`

### 5.7 `GET /api/v1/opsgraph/incidents/:incidentId/recommendations`

Purpose: list current runbook recommendations for an incident.

Auth: `viewer`

Response:

- `200 OK` with `RecommendationSummary[]`

### 5.7.1 `GET /api/v1/opsgraph/incidents/:incidentId/approval-tasks`

Purpose: list approval tasks currently linked to one incident.

Auth: `viewer`

Response:

- `200 OK` with `ApprovalTaskSummary[]`

### 5.7.2 `GET /api/v1/opsgraph/approval-tasks/:approvalTaskId`

Purpose: fetch one approval task by id.

Auth: `viewer`

Response:

- `200 OK` with `ApprovalTaskSummary`

### 5.8 `GET /api/v1/opsgraph/incidents/:incidentId/comms`

Purpose: list communication drafts for an incident.

Auth: `viewer`

Query params:

- `channel`
- `status`

Response:

- `200 OK` with `CommsDraftSummary[]`

### 5.9 `POST /api/v1/opsgraph/incidents/:incidentId/comms/:draftId/publish`

Purpose: publish an approved communication draft.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "expected_fact_set_version": 3,
  "approval_task_id": null
}
```

Rules:

1. If the draft channel/policy requires approval, `approval_task_id` must point to an `approved` task
2. `expected_fact_set_version` must equal the draft's stored fact set version

Response:

- `200 OK`

```json
{
  "data": {
    "draft_id": "uuid",
    "status": "published",
    "published_message_ref": "slack-msg-123"
  }
}
```

Errors:

- `COMM_DRAFT_STALE_FACT_SET`
- `APPROVAL_REQUIRED`
- `COMM_DRAFT_ALREADY_PUBLISHED`

### 5.10 `POST /api/v1/opsgraph/incidents/:incidentId/resolve`

Purpose: mark incident resolved and record closing summary.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "resolution_summary": "Rolled back deployment 123 and error rate returned to baseline.",
  "root_cause_fact_ids": ["uuid"],
  "expected_updated_at": "2026-03-16T09:20:00Z"
}
```

Response:

- `200 OK` with updated `IncidentSummary`

Errors:

- `INCIDENT_ALREADY_RESOLVED`
- `ROOT_CAUSE_FACT_REQUIRED`
- `CONFLICT_STALE_RESOURCE`

### 5.11 `POST /api/v1/opsgraph/incidents/:incidentId/close`

Purpose: close a resolved incident after communication and follow-up capture.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "close_reason": "Postmortem draft created and no further action pending.",
  "expected_updated_at": "2026-03-16T09:30:00Z"
}
```

Response:

- `200 OK` with updated `IncidentSummary`

Errors:

- `INCIDENT_NOT_RESOLVED`
- `CONFLICT_STALE_RESOURCE`

### 5.12 `GET /api/v1/opsgraph/incidents/:incidentId/postmortem`

Purpose: fetch the latest postmortem draft or final document.

Auth: `viewer`

Response:

- `200 OK`

```json
{
  "data": {
    "id": "uuid",
    "status": "draft",
    "fact_set_version": 4,
    "artifact_id": null,
    "replay_case_id": "uuid",
    "finalized_by_user_id": null,
    "finalized_at": null,
    "updated_at": "2026-03-16T09:40:00Z"
  }
}
```

### 5.12.1 `POST /api/v1/opsgraph/incidents/:incidentId/postmortem/finalize`

Purpose: mark the current postmortem as final and stamp finalization metadata.

Auth: `incident_commander` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "finalized_by_user_id": "uuid",
  "expected_updated_at": "2026-03-16T09:40:00Z"
}
```

Response:

- `200 OK` with updated `PostmortemSummary`

Errors:

- `CONFLICT_STALE_RESOURCE`

### 5.12.2 `GET /api/v1/opsgraph/postmortems`

Purpose: list postmortem drafts/finals for one workspace with optional incident and status filters.

Auth: `viewer`

Query params:

- `workspace_id`
- `incident_id`
- `status`
- `cursor`
- `limit`

Response:

- `200 OK` with paginated `PostmortemSummary[]`

### 5.13 `GET /api/v1/opsgraph/replay-cases`

Purpose: list replay cases created from retrospective or curated replay snapshots.

Auth: `viewer`

Query params:

- `workspace_id`
- `incident_id`

Response:

- `200 OK`

### 5.13.1 `GET /api/v1/opsgraph/replay-cases/:replayCaseId`

Purpose: fetch one replay case including its persisted input snapshot.

Auth: `viewer`

Response:

- `200 OK`

### 5.14 `POST /api/v1/opsgraph/replays/run`

Purpose: trigger replay for one incident or one replay case.

Auth: `product_admin` or stronger

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "incident_id": "uuid",
  "replay_case_id": null,
  "model_bundle_version": "opsgraph-v1.2"
}
```

Rules:

1. Exactly one of `incident_id` or `replay_case_id` must be provided

Response:

- `202 Accepted`

```json
{
  "data": {
    "replay_run_id": "uuid",
    "status": "queued"
  }
}
```

### 5.15 `GET /api/v1/opsgraph/replays`

Purpose: list replay runs for one workspace or one incident.

Auth: `viewer`

Query params:

- `workspace_id`
- `incident_id`
- `replay_case_id`
- `status`
- `cursor`
- `limit`

Response:

- `200 OK`

### 5.16 `GET /api/v1/opsgraph/replays/reports`

Purpose: list replay evaluation reports for one workspace with optional incident, replay run, or replay case filters.

Auth: `viewer`

Query params:

- `workspace_id`
- `incident_id`
- `replay_run_id`
- `replay_case_id`

Response:

- `200 OK`

## 6. SSE Contract

### 6.1 Topics

Supported `OpsGraph` topics:

1. `opsgraph.workspace.{workspaceId}`
2. `opsgraph.incident.{incidentId}`

### 6.2 Event Types

#### `opsgraph.signal.received`

Payload:

```json
{
  "incident_id": "uuid",
  "signal_id": "uuid",
  "status": "firing"
}
```

#### `opsgraph.incident.updated`

Payload:

```json
{
  "incident_id": "uuid",
  "severity": "sev1",
  "status": "investigating"
}
```

#### `opsgraph.context.ready`

Payload:

```json
{
  "incident_id": "uuid",
  "context_bundle_id": "uuid",
  "bundle_status": "ready"
}
```

#### `opsgraph.hypothesis.updated`

Payload:

```json
{
  "incident_id": "uuid",
  "top_hypothesis_id": "uuid",
  "hypothesis_count": 3
}
```

#### `opsgraph.approval.updated`

Producer: recommendation decision API

Payload:

```json
{
  "incident_id": "uuid",
  "approval_task_id": "uuid",
  "status": "approved"
}
```

#### `opsgraph.comms.updated`

Payload:

```json
{
  "incident_id": "uuid",
  "draft_id": "uuid",
  "status": "published"
}
```

## 7. Async Event Contract

### 7.1 Event Types

#### `opsgraph.signal.ingested`

Producer: webhook intake API

Payload:

```json
{
  "signal_id": "uuid",
  "source": "prometheus",
  "dedupe_key": "checkout-api:high-error-rate"
}
```

#### `opsgraph.incident.created`

Producer: webhook intake API

Payload:

```json
{
  "incident_id": "uuid",
  "incident_key": "INC-2026-0001",
  "severity": "sev1"
}
```

#### `opsgraph.incident.updated`

Producer: incident mutation APIs or workflow

Payload:

```json
{
  "incident_id": "uuid",
  "status": "mitigating",
  "severity": "sev1"
}
```

#### `opsgraph.context.ready`

Producer: enrichment worker

Payload:

```json
{
  "incident_id": "uuid",
  "context_bundle_id": "uuid",
  "bundle_status": "ready"
}
```

#### `opsgraph.hypothesis.generated`

Producer: hypothesis worker

Payload:

```json
{
  "incident_id": "uuid",
  "top_hypothesis_id": "uuid",
  "count": 3
}
```

#### `opsgraph.approval.requested`

Producer: recommendation/comms workflow

Payload:

```json
{
  "incident_id": "uuid",
  "approval_task_id": "uuid",
  "subject_type": "runbook_recommendation",
  "subject_id": "uuid"
}
```

#### `opsgraph.comms.ready`

Producer: comms worker

Payload:

```json
{
  "incident_id": "uuid",
  "draft_id": "uuid",
  "fact_set_version": 3,
  "channel_type": "internal_slack"
}
```

#### `opsgraph.postmortem.ready`

Producer: retrospective worker

Payload:

```json
{
  "incident_id": "uuid",
  "postmortem_id": "uuid",
  "fact_set_version": 4
}
```

#### `opsgraph.postmortem.updated`

Producer: postmortem finalization API

Payload:

```json
{
  "incident_id": "uuid",
  "postmortem_id": "uuid",
  "postmortem_status": "final"
}
```

## 8. Error Codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `INVALID_WEBHOOK_TOKEN` | `401` | Webhook secret missing or invalid |
| `INVALID_ALERT_PAYLOAD` | `422` | Prometheus/Grafana payload invalid |
| `SIGNAL_DUPLICATE` | `200` | Duplicate delivery accepted without new mutation |
| `INCIDENT_NOT_FOUND` | `404` | Incident missing or hidden |
| `INCIDENT_STATUS_CONFLICT` | `409` | Requested transition invalid |
| `FACT_VERSION_CONFLICT` | `409` | Fact set changed since client read |
| `INVALID_SOURCE_REFERENCE` | `422` | Fact source refs invalid |
| `HYPOTHESIS_STATUS_CONFLICT` | `409` | Hypothesis already terminal |
| `APPROVAL_REQUIRED` | `409` | Publish/action needs resolved approval task |
| `COMM_DRAFT_STALE_FACT_SET` | `409` | Current incident fact set has diverged |
| `COMM_DRAFT_ALREADY_PUBLISHED` | `409` | Draft already terminal |
| `INCIDENT_ALREADY_RESOLVED` | `409` | Incident already resolved |
| `INCIDENT_NOT_RESOLVED` | `409` | Close requested before resolve |
| `ROOT_CAUSE_FACT_REQUIRED` | `422` | Resolve requires at least one root cause fact |
| `REPLAY_RUN_NOT_EXECUTED` | `409` | Replay evaluation requested before execution completed |
| `REPLAY_STATUS_CONFLICT` | `409` | Replay status transition is invalid for the current run state |
| `REPLAY_EVALUATION_UNAVAILABLE` | `503` | Replay evaluation runtime dependencies unavailable |

## 9. Authorization Matrix

| Endpoint Family | Minimum Role |
| --- | --- |
| Incident list/read | `viewer` |
| Fact add/retract | `operator` |
| Severity override | `operator` |
| Hypothesis decision | `operator` |
| Recommendation read | `viewer` |
| Comms draft read | `viewer` |
| Comms publish | `operator` |
| Resolve/close incident | `operator` |
| Replay trigger | `product_admin` |
| Replay read | `viewer` |

## 10. Mapping to Data Model

1. Webhook APIs map to `signal`, `incident`, `incident_signal_link`, `timeline_event`
2. Incident read API maps to `incident`, `context_bundle`, `incident_fact`, `hypothesis`, `runbook_recommendation`, `comms_draft`, `timeline_event`
3. Fact APIs map to `incident_fact` and increment `incident.current_fact_set_version`
4. Hypothesis decision maps to `hypothesis`
5. Recommendation approval is bridged through shared `approval_task`
6. Comms publish maps to `comms_draft` and appends `timeline_event`
7. Resolve/close APIs map to `incident`, `incident_fact`, `timeline_event`, and later `postmortem`
8. Replay trigger and replay-case read APIs map to shared `replay_case` / `replay_run`
