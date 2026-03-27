# OpsGraph API and Event Contracts

- Version: v0.1
- Date: 2026-03-26
- Scope: `OpsGraph` REST, webhook, SSE, and async event contracts

## 1. Contract Summary

This document defines the implementation-grade interface for `OpsGraph`:

1. Alert webhook ingestion
2. Session auth issuance, refresh, current-user, and logout APIs
3. Session-scoped membership administration APIs
4. Incident list and incident workspace APIs
5. Fact, hypothesis, recommendation, and communication APIs
6. Incident resolution and replay APIs
7. OpsGraph-specific SSE and async event contracts

Shared approval, workflow, artifact, and feedback contracts are defined in the shared platform contract. Product-local auth/session contracts are defined below.

### Session Auth Surface

- `POST /api/v1/auth/session`
  - Purpose: issue a bearer access token plus an HTTP-only `refresh_token` cookie
  - Request: `{ "email": "...", "password": "...", "organization_slug": "acme" }`
- `POST /api/v1/auth/session/refresh`
  - Purpose: rotate the current refresh token cookie and mint a fresh bearer access token
- `DELETE /api/v1/auth/session/current`
  - Purpose: revoke the current session and clear the refresh cookie
- `GET /api/v1/me` and `GET /api/v1/auth/me`
  - Purpose: return the active user, active organization, and memberships for the current session
- `GET /api/v1/auth/memberships`
  - Purpose: list memberships in the current session organization
  - Auth: `product_admin` or stronger
- `POST /api/v1/auth/memberships`
  - Purpose: provision or reactivate one membership in the current session organization
  - Auth: `product_admin` or stronger
- `PATCH /api/v1/auth/memberships/:membershipId`
  - Purpose: update one membership role, status, or display name in the current session organization
  - Auth: `product_admin` or stronger

Notes:

1. Bearer session tokens do not require `X-Organization-Id`; tenant context is embedded in the session
2. Existing header-based auth remains supported for local/demo compatibility on business routes
3. Session management routes require a real session token and do not accept header-only fallback
4. Membership admin routes revoke active sessions in the current org when role or status changes

## 2. Domain Design Rules

1. Webhook ingestion is idempotent and must tolerate duplicate delivery
2. Facts, hypotheses, and recommendations are separate resource families
3. Communication publish requires fact-set safety checks
4. High-risk recommendation execution always routes through shared `approval_task`
5. Incident workspace reads are optimized for current-state snapshots plus append-only timeline

## 3. Domain Resource Shapes

### 3.0 `ManagedMembershipSummary`

```json
{
  "id": "membership-uuid",
  "organization_id": "org-1",
  "organization_slug": "acme",
  "organization_name": "Acme",
  "user": {
    "id": "user-1",
    "email": "operator@example.com",
    "display_name": "Ops Operator",
    "status": "active"
  },
  "role": "operator",
  "status": "active",
  "created_at": "2026-03-26T08:00:00Z",
  "updated_at": "2026-03-26T08:00:00Z"
}
```

### 3.0.1 Membership Admin Request Shapes

- `POST /api/v1/auth/memberships`

```json
{
  "email": "new-operator@example.com",
  "display_name": "New Operator",
  "role": "operator",
  "password": "opsgraph-demo-new"
}
```

- `PATCH /api/v1/auth/memberships/:membershipId`

```json
{
  "role": "viewer",
  "status": "suspended",
  "display_name": "Renamed Operator"
}
```

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

### 3.7 `TimelineEventSummary`

```json
{
  "id": "uuid",
  "kind": "fact_confirmed",
  "summary": "Checkout requests are failing for 27% of users.",
  "actor_type": "user",
  "actor_id": "uuid",
  "subject_type": "incident_fact",
  "subject_id": "uuid",
  "payload": {
    "fact_type": "impact",
    "fact_set_version": 3
  },
  "created_at": "2026-03-16T09:03:00Z"
}
```

### 3.8 `AuditLogSummary`

```json
{
  "id": "uuid",
  "incident_id": "uuid",
  "action_type": "incident.add_fact",
  "actor_type": "user",
  "actor_user_id": "uuid",
  "actor_role": "operator",
  "session_id": "uuid",
  "request_id": "req-123",
  "idempotency_key": "fact-add-1",
  "subject_type": "incident_fact",
  "subject_id": "uuid",
  "request_payload": {},
  "result_payload": {},
  "created_at": "2026-03-16T09:03:00Z"
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
- `shift_label` optional; when provided, `is_default` and `default_source` are resolved against that shift layer first and then fall back to the workspace default
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

### 5.7.3 `GET /api/v1/opsgraph/incidents/:incidentId/audit-logs`

Purpose: list incident-scoped operator audit logs for manual actions and replay-admin mutations.

Auth: `operator` or stronger

Query params:

- `action_type`
- `actor_user_id`
- `cursor`
- `limit`

Response:

- `200 OK` with paginated `AuditLogSummary[]`

### 5.7.4 `POST /api/v1/opsgraph/approvals/:approvalTaskId/decision`

Purpose: approve or reject one approval task and optionally orchestrate the linked downstream action.

Auth: `operator` or stronger

Headers:

- `Idempotency-Key` optional

Request body:

```json
{
  "decision": "approve",
  "comment": "Approved by incident commander.",
  "execute_recommendation": true,
  "publish_linked_drafts": true,
  "linked_draft_ids": [],
  "expected_fact_set_version": 3
}
```

Rules:

1. `reject` cannot be combined with execution or publish side effects
2. `execute_recommendation` only works when the approval task is linked to a recommendation
3. Publishing linked drafts requires `expected_fact_set_version`
4. Every selected draft must already be linked to the approval task

Response:

- `200 OK`

```json
{
  "data": {
    "approval_task": {
      "id": "approval-task-1",
      "incident_id": "incident-1",
      "recommendation_id": "recommendation-1",
      "status": "approved"
    },
    "recommendation": {
      "recommendation_id": "recommendation-1",
      "status": "executed",
      "approval_task_id": "approval-task-1",
      "approval_status": "approved"
    },
    "published_drafts": [
      {
        "draft_id": "draft-1",
        "status": "published",
        "published_message_ref": "internal_slack-msg-123"
      }
    ]
  }
}
```

Errors:

- `APPROVAL_STATUS_CONFLICT`
- `APPROVAL_DECISION_INVALID`
- `APPROVAL_EXECUTION_REQUIRES_RECOMMENDATION`
- `APPROVAL_PUBLISH_FACT_SET_REQUIRED`
- `APPROVAL_DRAFT_SELECTION_INVALID`
- `COMM_DRAFT_STALE_FACT_SET`
- `COMM_DRAFT_ALREADY_PUBLISHED`

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

Auth: `operator` or stronger

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

Notes:

1. Successful replay submissions are persisted to incident audit logs as `replay.start_run`

### 5.15 `POST /api/v1/opsgraph/replays/process-queued`

Purpose: process queued replay runs for one workspace in created-at order.

Auth: `product_admin` or stronger

Query params:

- `workspace_id`
- `limit` optional, defaults to `20`

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "queued_count": 3,
    "processed_count": 2,
    "completed_count": 2,
    "failed_count": 0,
    "skipped_count": 0,
    "remaining_queued_count": 1,
    "items": [
      {
        "id": "uuid",
        "status": "completed",
        "workflow_run_id": "uuid-replay"
      }
    ]
  }
}
```

Rules:

1. `limit` must be greater than or equal to `1`
2. Runs are selected from the queued set oldest-first by `created_at`
3. Each processed item records the same `replay.execute` audit trail as manual replay execution

Operational note:

1. `scripts/run_replay_worker.py` can call this route-equivalent service path once or in polling mode for local and CI replay queue processing
2. Supervisor-mode replay workers persist their latest heartbeat into the product repository so `/health` and runtime capabilities can report the last observed worker state
3. Runtime capabilities also expose a recent heartbeat window so product admins can inspect the latest `active` / `idle` / `retrying` transitions without reading worker logs
4. Replay-worker alerts default to `warning` at 1 consecutive failure and `critical` at 3 consecutive failures; override them with `OPSGRAPH_REPLAY_ALERT_WARNING_CONSECUTIVE_FAILURES` and `OPSGRAPH_REPLAY_ALERT_CRITICAL_CONSECUTIVE_FAILURES`
5. Replay-worker alert policy edits are recorded in the replay-admin audit log for the selected workspace

### 5.15.1 `GET /api/v1/opsgraph/replays/worker-alert-policy`

Purpose: read the effective replay-worker alert policy for one workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "warning_consecutive_failures": 1,
    "critical_consecutive_failures": 3,
    "source": "default",
    "updated_at": null
  }
}
```

### 5.15.2 `PATCH /api/v1/opsgraph/replays/worker-alert-policy`

Purpose: set or reset the replay-worker alert policy for one workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Request body:

```json
{
  "warning_consecutive_failures": 2,
  "critical_consecutive_failures": 4
}
```

Rules:

1. `warning_consecutive_failures` must be greater than or equal to `1`
2. `critical_consecutive_failures` must be greater than or equal to `warning_consecutive_failures`
3. Sending the runtime default threshold pair removes the workspace override and returns `source = "default"`

### 5.15.3 `GET /api/v1/opsgraph/replays/worker-monitor-presets`

Purpose: list shared replay-worker monitor presets for one workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Response:

- `200 OK`

```json
{
  "data": [
    {
      "workspace_id": "ops-ws-1",
      "preset_name": "night-shift",
      "history_limit": 10,
      "actor_user_id": "user-admin-1",
      "request_id": "req-replay-policy-1",
      "policy_audit_limit": 5,
      "policy_audit_copy_format": "markdown",
      "policy_audit_include_summary": true,
      "is_default": true,
      "default_source": "shift_default"
    }
  ]
}
```

### 5.15.3a `GET /api/v1/opsgraph/replays/worker-monitor-shift-schedule`

Purpose: read the workspace shift table used by replay-worker monitor auto shift resolution.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "timezone": "UTC",
    "windows": [
      {"shift_label": "day", "start_time": "08:00", "end_time": "20:00"},
      {"shift_label": "night", "start_time": "20:00", "end_time": "08:00"}
    ],
    "date_overrides": [
      {
        "date": "2026-12-25",
        "note": "Holiday coverage",
        "windows": [
          {"shift_label": "holiday", "start_time": "10:00", "end_time": "14:00"}
        ]
      }
    ],
    "date_range_overrides": [
      {
        "start_date": "2026-12-26",
        "end_date": "2026-12-31",
        "note": "Change freeze week",
        "windows": [
          {"shift_label": "freeze", "start_time": "09:00", "end_time": "18:00"}
        ]
      }
    ],
    "updated_at": "2026-03-27T09:00:01Z"
  }
}
```

### 5.15.3b `PUT /api/v1/opsgraph/replays/worker-monitor-shift-schedule`

Purpose: create or replace the workspace shift table used by replay-worker monitor auto shift resolution.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Rules:

1. `timezone` must be a valid IANA timezone such as `UTC` or `Asia/Shanghai`
2. `windows[].shift_label` must not be blank and must be unique within one schedule
3. `windows[].start_time` and `windows[].end_time` must use `HH:MM` 24-hour format
4. Overnight windows are allowed, for example `20:00 -> 08:00`
5. `date_overrides[].date` must use `YYYY-MM-DD` format and must be unique within one schedule
6. `date_range_overrides[].start_date` and `date_range_overrides[].end_date` must use `YYYY-MM-DD` format and ranges must not overlap each other
7. `date_overrides[].windows` and `date_range_overrides[].windows` use the same window contract as the base schedule
8. Resolution order is exact date override first, then date-range override, then the base schedule
9. When any override layer matches one local date it fully replaces the lower-priority schedule for that date, including the case where no override window matches the current hour
10. Successful writes are persisted to replay-admin audit logs as `replay.update_worker_monitor_shift_schedule`

### 5.15.3c `DELETE /api/v1/opsgraph/replays/worker-monitor-shift-schedule`

Purpose: clear the workspace shift table used by replay-worker monitor auto shift resolution.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Rules:

1. Successful clears are persisted to replay-admin audit logs as `replay.clear_worker_monitor_shift_schedule`

### 5.15.3d `GET /api/v1/opsgraph/replays/worker-monitor-resolved-shift`

Purpose: resolve the currently active shift label from the workspace shift table.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required
- `at` optional ISO-8601 timestamp; defaults to server current time

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "timezone": "UTC",
    "evaluated_at": "2026-03-27T21:00:00Z",
    "shift_label": "freeze",
    "source": "date_range_override",
    "matched_window": {
      "shift_label": "freeze",
      "start_time": "09:00",
      "end_time": "18:00"
    },
    "override_date": null,
    "override_range_start_date": "2026-12-26",
    "override_range_end_date": "2026-12-31",
    "override_note": "Change freeze week",
    "updated_at": "2026-03-27T09:00:01Z"
  }
}
```

### 5.15.4 `PUT /api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}`

Purpose: create or update one shared replay-worker monitor preset for a workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Request body:

```json
{
  "history_limit": 10,
  "actor_user_id": "user-admin-1",
  "request_id": "req-replay-policy-1",
  "policy_audit_limit": 5,
  "policy_audit_copy_format": "markdown",
  "policy_audit_include_summary": true
}
```

Rules:

1. `preset_name` must not be blank
2. `history_limit` must be greater than or equal to `1`
3. `policy_audit_limit` must be greater than or equal to `1`
4. `policy_audit_copy_format` must be one of `plain`, `markdown`, or `slack`
5. Successful writes are persisted to replay-admin audit logs as `replay.upsert_worker_monitor_preset`

### 5.15.5 `DELETE /api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}`

Purpose: delete one shared replay-worker monitor preset for a workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required

Response:

- `200 OK`

Rules:

1. Successful deletes are persisted to replay-admin audit logs as `replay.delete_worker_monitor_preset`

### 5.15.6 `GET /api/v1/opsgraph/replays/worker-monitor-default-preset`

Purpose: read the effective replay-worker monitor default preset for one workspace, optionally scoped to one shift label.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required
- `shift_label` optional

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "preset_name": "night-shift",
    "shift_label": "night",
    "source": "shift_default",
    "updated_at": "2026-03-27T09:00:04Z",
    "cleared": false
  }
}
```

### 5.15.7 `PUT /api/v1/opsgraph/replays/worker-monitor-default-preset/{preset_name}`

Purpose: mark one shared replay-worker monitor preset as the default preset for either the workspace or one shift layer.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required
- `shift_label` optional

Rules:

1. `preset_name` must already exist as a shared workspace preset
2. When `shift_label` is present the write only affects that shift layer
3. Successful writes are persisted to replay-admin audit logs as `replay.set_worker_monitor_default_preset`

### 5.15.8 `DELETE /api/v1/opsgraph/replays/worker-monitor-default-preset`

Purpose: clear the targeted replay-worker monitor default layer for one workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required
- `shift_label` optional

Rules:

1. When `shift_label` is present only that shift override is cleared; the workspace default remains intact
2. Successful clears are persisted to replay-admin audit logs as `replay.clear_worker_monitor_default_preset`

### 5.15.9 `GET /api/v1/opsgraph/replays/audit-logs`

Purpose: list replay-admin audit records for one workspace.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` required
- `action_type` optional
- `actor_user_id` optional
- `request_id` optional
- `cursor` optional
- `limit` optional, default `20`

Response:

- `200 OK`

```json
{
  "data": [
    {
      "id": "replay-audit-1",
      "workspace_id": "ops-ws-1",
      "action_type": "replay.update_worker_alert_policy",
      "subject_type": "replay_worker_alert_policy",
      "subject_id": "ops-ws-1"
    }
  ]
}
```

### 5.15.10 `GET /api/v1/opsgraph/replays/worker-status`

Purpose: read the latest persisted replay worker status plus a recent heartbeat window.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` optional
- `history_limit` optional, defaults to `10`

Response:

- `200 OK`

```json
{
  "data": {
    "workspace_id": "ops-ws-1",
    "current": {
      "status": "idle",
      "remaining_queued_count": 0
    },
    "policy": {
      "workspace_id": "ops-ws-1",
      "warning_consecutive_failures": 1,
      "critical_consecutive_failures": 3,
      "source": "default"
    },
    "history": [
      {
        "status": "idle",
        "iteration": 2
      },
      {
        "status": "active",
        "iteration": 1
      }
    ]
  }
}
```

Rules:

1. `history_limit` must be greater than or equal to `1`
2. `history` is ordered newest-first

### 5.15.11 `GET /api/v1/opsgraph/replays/worker-status/stream`

Purpose: subscribe to replay worker status snapshots via SSE.

Auth: `product_admin` or stronger

Query params:

- `workspace_id` optional
- `history_limit` optional, defaults to `10`

Response:

- `200 OK`
- `Content-Type: text/event-stream`

Event contract:

1. Event name is `opsgraph.replay_worker.status`
2. Event payload matches `GET /api/v1/opsgraph/replays/worker-status`
3. Event ids are derived from the latest persisted heartbeat timestamp for the selected workspace

Monitoring page:

1. `GET /opsgraph/replays/worker-monitor` serves a product-admin HTML monitor that uses the same-origin SSE stream, highlights the current replay worker alert, shows the latest persisted failure details, displays recent policy-change audit entries with actor/request quick filters, supports preset scope switching between workspace-shared presets and browser-local presets, named preset save/load/delete for workspace, filter, and export settings, supports marking or clearing either the workspace default preset or one shift-specific default layer via `Shift Label`, adds `Shift Source` manual/auto selection, resolves the current shift from the workspace shift table when auto mode is active, surfaces exact-date and date-range override matches in the shift status line, automatically applies the effective default preset on first load in workspace scope when no explicit filter override is present, exposes an inline `Shift Schedule` editor backed by `GET/PUT/DELETE /api/v1/opsgraph/replays/worker-monitor-shift-schedule` for timezone, base windows, exact-date overrides, and range overrides, adds structured quick-add/remove controls for base windows plus date/range override windows, supports in-draft up/down reordering and row-to-form edit actions for those windows, adds direct copy/export/import actions for standalone shift schedule JSON, shows an import preview before imported JSON replaces the current draft, includes a detailed per-window diff for added/removed/reordered base/date/range entries, keeps advanced JSON arrays available for bulk edits, exposes copy-request/copy-filter-link/copy-latest-context/row-context actions with `Plain`/`Markdown`/`Slack` formatting, whole-window/latest-row/per-row JSON/CSV export with optional monitor summary metadata including the active copy format, preset scope, shift source, shift label, resolved shift label, resolved override date/range/note, preset name, and default source, alert/status-aware filenames, and embedded monitor return links, inline request/result payload expansion, fresh-row highlighting for newly recorded policy edits, adjustable row window, and older/newest paging controls, and lets product admins edit or reset the workspace alert thresholds in place

### 5.16 `GET /api/v1/opsgraph/replays`

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

### 5.17 `GET /api/v1/opsgraph/replays/reports`

Purpose: list replay evaluation reports for one workspace with optional incident, replay run, or replay case filters.

Auth: `viewer`

Query params:

- `workspace_id`
- `incident_id`
- `replay_run_id`
- `replay_case_id`

Response:

- `200 OK`

Notes:

1. Replay baseline capture, replay status mutation, replay execute, replay queued-batch processing, and replay evaluate flows are also persisted to incident audit logs as `replay.capture_baseline`, `replay.update_status`, `replay.execute`, and `replay.evaluate`

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
| `AUTH_REQUIRED` | `401` | Bearer token missing for a protected route |
| `TENANT_CONTEXT_REQUIRED` | `400` | Header-mode auth call omitted `X-Organization-Id` |
| `AUTH_INVALID_CREDENTIALS` | `401` | Login, bearer token, or refresh token is invalid |
| `AUTH_SESSION_EXPIRED` | `401` | Session access or refresh token expired |
| `AUTH_SESSION_REVOKED` | `401` | Session was explicitly revoked or rotated out |
| `AUTH_SESSION_REQUIRED` | `401` | Endpoint requires a real session-backed login |

## 9. Authorization Matrix

| Endpoint Family | Minimum Role |
| --- | --- |
| Session create / refresh | Public |
| Current user / session revoke | `viewer` via session token |
| Incident list/read | `viewer` |
| Fact add/retract | `operator` |
| Severity override | `operator` |
| Hypothesis decision | `operator` |
| Recommendation read | `viewer` |
| Incident audit log read | `operator` |
| Comms draft read | `viewer` |
| Comms publish | `operator` |
| Resolve/close incident | `operator` |
| Postmortem finalize | `operator` |
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
