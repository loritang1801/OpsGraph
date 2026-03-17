# Shared Platform API and Event Contracts

- Version: v0.1
- Date: 2026-03-16
- Scope: Shared REST, SSE, and async event contracts for `AuditFlow` and `OpsGraph`

## 1. Contract Summary

This document defines the common interface layer used by both products:

1. Authentication and session contracts
2. Shared REST envelopes and pagination rules
3. Approval, workflow, artifact, and feedback APIs
4. Shared SSE event stream contract
5. Shared async event payloads and outbox shape

It is intentionally implementation-grade, but not expressed as OpenAPI YAML.

## 2. Global API Conventions

### 2.1 Base Path

- REST base path: `/api/v1`
- SSE base path: `/api/v1/events/stream`

### 2.2 Authentication

v1 uses short-lived access token + long-lived refresh session.

- Access token transport: `Authorization: Bearer <access_token>`
- Refresh token transport: secure HTTP-only cookie `refresh_token`
- Tenant selection header: `X-Organization-Id: <uuid>`

Rules:

1. All authenticated API calls require `Authorization`
2. All tenant-scoped calls require `X-Organization-Id`
3. If the user is not a member of the provided organization, return `403`

### 2.3 Common Headers

| Header | Required | Purpose |
| --- | --- | --- |
| `Authorization` | Yes, except public login/webhook endpoints | Access token |
| `X-Organization-Id` | Yes for tenant APIs | Active organization |
| `X-Request-Id` | Recommended | Client-provided trace id |
| `Idempotency-Key` | Required for selected POST endpoints | Safe retries |
| `Last-Event-ID` | Optional for SSE reconnect | Resume live event stream |

### 2.4 Success Envelope

All JSON success responses use:

```json
{
  "data": {},
  "meta": {
    "request_id": "req_123",
    "next_cursor": null,
    "has_more": false
  }
}
```

Rules:

1. `meta.next_cursor` appears only on paginated list endpoints
2. Binary artifact download endpoints are the only exception
3. Create actions may also include `meta.workflow_run_id`

### 2.5 Error Envelope

All JSON errors use:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Field `cycle_name` is required.",
    "details": {
      "field": "cycle_name"
    },
    "retryable": false
  },
  "meta": {
    "request_id": "req_123"
  }
}
```

### 2.6 Cursor Pagination

Cursor pagination is the default for list endpoints.

Request parameters:

- `cursor`
- `limit`

Rules:

1. Default `limit` is `20`
2. Maximum `limit` is `100`
3. Cursor format is opaque to clients
4. Cursors are stable only for the specific endpoint and sort order

### 2.7 Timestamp and ID Format

1. All ids are `UUID` strings
2. All timestamps are RFC 3339 UTC strings
3. Enum values are lowercase snake_case unless explicitly documented otherwise

## 3. Shared Resource Shapes

### 3.1 `SessionResponse`

```json
{
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "Alice"
  },
  "active_organization": {
    "id": "uuid",
    "name": "Acme",
    "slug": "acme"
  },
  "memberships": [
    {
      "organization_id": "uuid",
      "role": "org_admin",
      "status": "active"
    }
  ],
  "access_token": "jwt-or-opaque-token",
  "expires_at": "2026-03-16T09:00:00Z"
}
```

### 3.2 `WorkspaceSummary`

```json
{
  "id": "uuid",
  "workspace_type": "auditflow",
  "name": "Acme SOC2",
  "slug": "acme-soc2",
  "status": "active",
  "created_at": "2026-03-16T09:00:00Z"
}
```

### 3.3 `WorkflowRunSummary`

```json
{
  "id": "uuid",
  "workflow_type": "auditflow_cycle",
  "subject_type": "audit_cycle",
  "subject_id": "uuid",
  "status": "running",
  "current_state": "mapping",
  "started_at": "2026-03-16T09:00:00Z",
  "ended_at": null,
  "error_code": null
}
```

### 3.4 `ApprovalTaskSummary`

```json
{
  "id": "uuid",
  "approval_type": "publish_comms",
  "status": "pending",
  "subject_type": "comms_draft",
  "subject_id": "uuid",
  "policy_code": "opsgraph.high_risk_publish",
  "assigned_to_user_id": "uuid",
  "requested_at": "2026-03-16T09:00:00Z",
  "expires_at": null
}
```

### 3.5 `ArtifactSummary`

```json
{
  "id": "uuid",
  "artifact_type": "export_package",
  "status": "active",
  "display_name": "audit-package-v3.zip",
  "mime_type": "application/zip",
  "byte_size": 1048576,
  "created_at": "2026-03-16T09:00:00Z"
}
```

## 4. Shared REST API

### 4.1 `POST /api/v1/auth/session`

Purpose: create a login session and issue access token.

Auth: none

Request body:

```json
{
  "email": "user@example.com",
  "password": "plain-text-password",
  "organization_slug": "acme"
}
```

Response:

- `200 OK` with `SessionResponse`
- Sets `refresh_token` secure HTTP-only cookie

Errors:

- `AUTH_INVALID_CREDENTIALS`
- `AUTH_ORGANIZATION_NOT_ACCESSIBLE`
- `AUTH_USER_DISABLED`

### 4.2 `POST /api/v1/auth/session/refresh`

Purpose: mint a new access token using refresh session cookie.

Auth: refresh cookie

Request body: none

Response:

- `200 OK` with `SessionResponse`

Errors:

- `AUTH_REFRESH_EXPIRED`
- `AUTH_SESSION_REVOKED`

### 4.3 `DELETE /api/v1/auth/session/current`

Purpose: revoke current refresh session.

Auth: access token

Response:

- `204 No Content`

### 4.4 `GET /api/v1/me`

Purpose: return current user and active tenant context.

Auth: access token

Response:

- `200 OK`

```json
{
  "data": {
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "display_name": "Alice"
    },
    "active_organization": {
      "id": "uuid",
      "name": "Acme",
      "slug": "acme"
    },
    "memberships": []
  }
}
```

### 4.5 `GET /api/v1/organizations/:orgId/workspaces`

Purpose: list workspaces visible to current user in one organization.

Auth: access token

Query params:

- `workspace_type`
- `status`
- `cursor`
- `limit`

Response:

- `200 OK` with paginated `WorkspaceSummary[]`

### 4.6 `POST /api/v1/workflows/:workflowType/runs`

Purpose: manually trigger a workflow run.

Auth: access token

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "workspace_id": "uuid",
  "subject_type": "audit_cycle",
  "subject_id": "uuid",
  "input": {
    "force_recompute": false
  }
}
```

Response:

- `202 Accepted`

```json
{
  "data": {
    "workflow_run": {
      "id": "uuid",
      "workflow_type": "auditflow_cycle",
      "status": "queued"
    }
  },
  "meta": {
    "request_id": "req_123",
    "workflow_run_id": "uuid"
  }
}
```

Errors:

- `WORKFLOW_TYPE_UNSUPPORTED`
- `WORKFLOW_SUBJECT_NOT_FOUND`
- `IDEMPOTENCY_CONFLICT`

### 4.7 `GET /api/v1/workflow-runs/:runId`

Purpose: fetch workflow run summary and terminal status.

Auth: access token

Response:

- `200 OK` with `WorkflowRunSummary` plus run metadata

### 4.8 `GET /api/v1/workflow-runs/:runId/steps`

Purpose: fetch ordered step execution history for one run.

Auth: access token

Response:

- `200 OK`

```json
{
  "data": [
    {
      "id": "uuid",
      "step_name": "mapping",
      "attempt_number": 1,
      "status": "completed",
      "latency_ms": 1820,
      "started_at": "2026-03-16T09:00:00Z",
      "ended_at": "2026-03-16T09:00:01Z"
    }
  ]
}
```

### 4.9 `GET /api/v1/approval-tasks`

Purpose: list approval tasks for current user or workspace.

Auth: access token

Query params:

- `status`
- `workspace_id`
- `subject_type`
- `subject_id`
- `assigned_to=me|all`
- `cursor`
- `limit`

Response:

- `200 OK` with paginated `ApprovalTaskSummary[]`

### 4.10 `GET /api/v1/approval-tasks/:taskId`

Purpose: fetch approval details.

Auth: access token

Response:

- `200 OK`

```json
{
  "data": {
    "id": "uuid",
    "approval_type": "review_mapping",
    "status": "pending",
    "payload": {},
    "subject_type": "evidence_mapping",
    "subject_id": "uuid",
    "requested_at": "2026-03-16T09:00:00Z"
  }
}
```

### 4.11 `POST /api/v1/approval-tasks/:taskId/decision`

Purpose: resolve an approval task.

Auth: access token

Headers:

- `Idempotency-Key` required

Request body:

```json
{
  "decision": "approved",
  "comment": "Reviewed and accepted."
}
```

Response:

- `200 OK` with updated `ApprovalTaskSummary`

Errors:

- `APPROVAL_ALREADY_RESOLVED`
- `APPROVAL_NOT_ASSIGNED`
- `CONFLICT_STALE_RESOURCE`

### 4.12 `GET /api/v1/artifacts/:artifactId`

Purpose: fetch artifact metadata.

Auth: access token

Response:

- `200 OK` with `ArtifactSummary` plus provenance metadata

### 4.13 `GET /api/v1/artifacts/:artifactId/content`

Purpose: download or redirect to artifact content.

Auth: access token

Response:

- `302 Found` to signed object store URL
- or `200 OK` binary stream for small inline artifacts

Errors:

- `ARTIFACT_NOT_FOUND`
- `ARTIFACT_FORBIDDEN`

### 4.14 `POST /api/v1/feedback`

Purpose: record normalized human feedback on any domain object.

Auth: access token

Request body:

```json
{
  "workspace_id": "uuid",
  "subject_type": "evidence_mapping",
  "subject_id": "uuid",
  "feedback_type": "reject",
  "label": {
    "reason": "unsupported_claim"
  },
  "comment": "The citation does not prove the control."
}
```

Response:

- `201 Created`

```json
{
  "data": {
    "feedback_id": "uuid"
  }
}
```

### 4.15 `GET /api/v1/events/stream`

Purpose: subscribe to live events for one workspace or one subject.

Auth: access token

Query params:

- `workspace_id` required
- `topic` optional
- `subject_type` optional
- `subject_id` optional

Response:

- `200 OK` with `text/event-stream`

Rules:

1. Server sends heartbeat every `15s`
2. Client may reconnect with `Last-Event-ID`
3. Server may drop events older than retention window; in that case client must refetch current page state

## 5. SSE Contract

### 5.1 Event Envelope

Each SSE message uses:

```text
id: evt_123
event: workflow.step.completed
data: {
  "event_id": "evt_123",
  "event_type": "workflow.step.completed",
  "organization_id": "uuid",
  "workspace_id": "uuid",
  "subject_type": "audit_cycle",
  "subject_id": "uuid",
  "occurred_at": "2026-03-16T09:00:00Z",
  "payload": {}
}
```

### 5.2 Supported Topics

1. `workspace`
2. `workflow`
3. `approval`
4. `artifact`
5. Domain-specific topics defined in product docs

## 6. Shared Async Event Contract

### 6.1 Base Event Shape

All async events published from outbox must contain:

```json
{
  "event_id": "uuid",
  "event_type": "workflow.run.started",
  "organization_id": "uuid",
  "workspace_id": "uuid",
  "aggregate_type": "workflow_run",
  "aggregate_id": "uuid",
  "workflow_run_id": "uuid",
  "causation_id": "uuid",
  "occurred_at": "2026-03-16T09:00:00Z",
  "payload": {}
}
```

### 6.2 Shared Event Types

#### `workflow.run.started`

Producer: workflow orchestrator

Payload:

```json
{
  "workflow_type": "auditflow_cycle",
  "subject_type": "audit_cycle",
  "subject_id": "uuid",
  "current_state": "ingestion"
}
```

#### `workflow.step.completed`

Producer: worker/orchestrator

Payload:

```json
{
  "step_name": "mapping",
  "attempt_number": 1,
  "latency_ms": 1820,
  "next_state": "challenge"
}
```

#### `workflow.step.failed`

Producer: worker/orchestrator

Payload:

```json
{
  "step_name": "normalization",
  "attempt_number": 2,
  "error_code": "PARSER_TIMEOUT",
  "retryable": true
}
```

#### `approval.requested`

Producer: domain workflow

Payload:

```json
{
  "approval_task_id": "uuid",
  "approval_type": "publish_comms",
  "subject_type": "comms_draft",
  "subject_id": "uuid"
}
```

#### `approval.resolved`

Producer: approval API

Payload:

```json
{
  "approval_task_id": "uuid",
  "decision": "approved",
  "decision_by_user_id": "uuid"
}
```

#### `artifact.created`

Producer: artifact service or worker

Payload:

```json
{
  "artifact_id": "uuid",
  "artifact_type": "export_package",
  "display_name": "package-v3.zip"
}
```

#### `feedback.recorded`

Producer: feedback API

Payload:

```json
{
  "feedback_id": "uuid",
  "subject_type": "evidence_mapping",
  "subject_id": "uuid",
  "feedback_type": "reject"
}
```

#### `memory.updated`

Producer: memory service

Payload:

```json
{
  "memory_id": "uuid",
  "scope": "organization",
  "subject_type": "control_family",
  "subject_id": "uuid"
}
```

#### `replay.run.completed`

Producer: replay worker

Payload:

```json
{
  "replay_run_id": "uuid",
  "replay_case_id": "uuid",
  "status": "completed",
  "score": {
    "pass_rate": 0.92
  }
}
```

## 7. Background Job Trigger Contract

The shared outbox row consumed by workers must minimally carry:

```json
{
  "event_type": "auditflow.mapping.generated",
  "aggregate_type": "audit_cycle",
  "aggregate_id": "uuid",
  "organization_id": "uuid",
  "workspace_id": "uuid",
  "payload": {},
  "available_at": "2026-03-16T09:00:00Z"
}
```

Retry rules:

1. Retryable failures:
   - connector timeout
   - temporary model provider failure
   - transient object store/network issue
2. Non-retryable failures:
   - validation/schema mismatch
   - permission denied
   - missing required domain object

## 8. Shared Error Codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | `401` | Email/password invalid |
| `AUTH_REFRESH_EXPIRED` | `401` | Refresh session expired |
| `AUTH_FORBIDDEN` | `403` | User lacks required role |
| `RESOURCE_NOT_FOUND` | `404` | Resource missing or hidden |
| `VALIDATION_ERROR` | `422` | Request payload invalid |
| `IDEMPOTENCY_CONFLICT` | `409` | Same idempotency key, different payload |
| `CONFLICT_STALE_RESOURCE` | `409` | Resource changed since client read |
| `APPROVAL_ALREADY_RESOLVED` | `409` | Approval already terminal |
| `RATE_LIMITED` | `429` | Request or connector rate limit |
| `CONNECTOR_UNAVAILABLE` | `503` | Upstream integration unavailable |
| `INTERNAL_RETRYABLE` | `503` | Retryable internal failure |
| `INTERNAL_FATAL` | `500` | Non-retryable internal error |

## 9. Authorization Matrix

Minimum shared permissions:

| Endpoint Family | Minimum Role |
| --- | --- |
| `GET /me` | `viewer` |
| Workspace list/read | `viewer` |
| Manual workflow trigger | `product_admin` or product-specific owner |
| Approval list/read | `reviewer` or `operator` depending on product |
| Approval decision | Assigned approver or stronger |
| Artifact metadata/download | Product-specific read permission |
| Feedback submit | `reviewer` or `operator` |
| SSE stream | Same permission as underlying workspace read |

## 10. Mapping to Data Model

1. Auth APIs map to `app_user`, `organization_membership`, `auth_session`
2. Workspace APIs map to `workspace`
3. Workflow APIs map to `workflow_run`, `workflow_step_run`, `workflow_checkpoint`
4. Approval APIs map to `approval_task`
5. Artifact APIs map to `artifact`
6. Feedback API maps to `feedback_event`
7. SSE and async events are sourced from `outbox_event` and workflow/activity side effects
