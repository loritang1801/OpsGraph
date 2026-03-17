# Shared Platform Database Design

- Version: v0.1
- Date: 2026-03-16
- Scope: Shared data model and database design for `AuditFlow` and `OpsGraph`

## 1. Database Overview

This document defines the physical data model for the shared platform kernel used by both products.

- Database: `PostgreSQL 16+`
- Extensions: `pgvector`, `pgcrypto` or UUID generation extension
- Timezone: `UTC`
- Character encoding: `UTF-8`
- ID strategy: `UUID`
- ORM target: `SQLAlchemy 2.x`
- Migration tool: `Alembic`

## 2. Modeling Conventions

### 2.1 Global Rules

1. All primary keys use `UUID`.
2. All time columns use `TIMESTAMPTZ`.
3. All tenant-scoped tables must include `organization_id`.
4. All mutable tables include `created_at` and `updated_at`.
5. Append-only or audit tables do not update rows except for rare operational flags.
6. `JSONB` is allowed only for:
   - external payload snapshots
   - workflow/model metadata
   - flexible evidence or connector metadata
7. Soft delete is limited to user-facing mutable tables; audit/workflow/review logs remain immutable.

### 2.2 Naming Rules

1. Use `snake_case` for tables and columns.
2. Use singular table names.
3. Logical entity `user` is implemented as table `app_user` to avoid ambiguity.
4. Domain tables keep module-specific prefixes where useful, for example `audit_cycle`.

### 2.3 Common Audit Columns

Unless otherwise noted, mutable tables include:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | Primary key |
| `created_at` | `TIMESTAMPTZ` | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | Updated on every write |

Tenant-scoped tables also include:

| Column | Type | Notes |
| --- | --- | --- |
| `organization_id` | `UUID` | FK to `organization.id`, required |

## 3. Shared Enumerations

### 3.1 Membership Role

- `org_admin`
- `product_admin`
- `reviewer`
- `operator`
- `viewer`

### 3.2 Workspace Type

- `auditflow`
- `opsgraph`

### 3.3 Connection Status

- `active`
- `disabled`
- `error`
- `reauth_required`

### 3.4 Workflow Status

- `queued`
- `running`
- `waiting_for_input`
- `waiting_for_approval`
- `completed`
- `failed`
- `cancelled`
- `attention_required`

### 3.5 Workflow Step Status

- `queued`
- `running`
- `completed`
- `failed`
- `skipped`
- `cancelled`

### 3.6 Approval Status

- `pending`
- `approved`
- `rejected`
- `expired`
- `cancelled`

### 3.7 Artifact Type

- `raw_file`
- `normalized_text`
- `export_package`
- `generated_report`
- `draft_message`
- `trace_dump`

### 3.8 Artifact Status

- `active`
- `superseded`
- `failed`
- `deleted`

### 3.9 Memory Scope

- `organization`
- `workspace`
- `service`
- `audit_cycle`
- `incident`

### 3.10 Replay Status

- `queued`
- `running`
- `completed`
- `failed`

## 4. Core Tables

### 4.1 `organization`

Purpose: tenant root and isolation boundary.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `name` | `VARCHAR(200)` | No | Display name |
| `slug` | `VARCHAR(100)` | No | Unique tenant slug |
| `status` | `VARCHAR(30)` | No | `active`, `trial`, `suspended`, `archived` |
| `billing_plan` | `VARCHAR(50)` | Yes | Optional commercial tier |
| `settings_json` | `JSONB` | Yes | Org-wide feature/config flags |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Unique index on `slug`
2. Index on `status`

### 4.2 `app_user`

Purpose: application identity.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `email` | `VARCHAR(255)` | No | Unique, lowercased |
| `display_name` | `VARCHAR(200)` | No | User-facing name |
| `password_hash` | `VARCHAR(255)` | Yes | Nullable if SSO-only later |
| `status` | `VARCHAR(30)` | No | `active`, `invited`, `disabled` |
| `last_login_at` | `TIMESTAMPTZ` | Yes | Last successful login |
| `profile_json` | `JSONB` | Yes | Avatar URL, locale, etc. |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Unique index on `email`
2. Index on `status`

### 4.3 `organization_membership`

Purpose: RBAC mapping between users and organizations.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `user_id` | `UUID` | No | FK `app_user.id` |
| `role` | `VARCHAR(30)` | No | Membership role enum |
| `status` | `VARCHAR(30)` | No | `active`, `invited`, `revoked` |
| `invited_by_user_id` | `UUID` | Yes | FK `app_user.id` |
| `joined_at` | `TIMESTAMPTZ` | Yes | Set once active |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(organization_id, user_id)`

Indexes:

1. Index on `(organization_id, role, status)`
2. Index on `(user_id, status)`

### 4.4 `workspace`

Purpose: product-scoped working container under one organization.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_type` | `VARCHAR(30)` | No | `auditflow` or `opsgraph` |
| `name` | `VARCHAR(200)` | No | Workspace name |
| `slug` | `VARCHAR(100)` | No | Unique per org |
| `status` | `VARCHAR(30)` | No | `active`, `archived` |
| `default_timezone` | `VARCHAR(50)` | Yes | Optional display timezone |
| `settings_json` | `JSONB` | Yes | Product-specific workspace config |
| `created_by_user_id` | `UUID` | No | FK `app_user.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(organization_id, workspace_type, slug)`

Indexes:

1. Index on `(organization_id, workspace_type, status)`

### 4.5 `auth_session`

Purpose: access/refresh session persistence.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `user_id` | `UUID` | No | FK `app_user.id` |
| `organization_id` | `UUID` | No | Active tenant at login time |
| `refresh_token_hash` | `VARCHAR(255)` | No | Never store raw token |
| `session_status` | `VARCHAR(30)` | No | `active`, `revoked`, `expired` |
| `expires_at` | `TIMESTAMPTZ` | No | Refresh token expiry |
| `last_seen_at` | `TIMESTAMPTZ` | Yes | Updated on token refresh |
| `ip_address` | `INET` | Yes | Optional security record |
| `user_agent` | `TEXT` | Yes | Optional client record |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(user_id, session_status)`
2. Index on `(organization_id, session_status)`
3. Index on `expires_at`

### 4.6 `external_connection`

Purpose: connector configuration and credential metadata.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | Yes | Nullable when shared across workspace |
| `provider` | `VARCHAR(50)` | No | `jira`, `confluence`, `github`, `slack`, etc. |
| `display_name` | `VARCHAR(200)` | No | Friendly connector name |
| `status` | `VARCHAR(30)` | No | Connection status enum |
| `auth_type` | `VARCHAR(30)` | No | `oauth`, `api_token`, `webhook_secret` |
| `encrypted_secret_ref` | `TEXT` | No | Encrypted secret or external secret ref |
| `config_json` | `JSONB` | Yes | Base URL, project filters, channel config |
| `last_success_at` | `TIMESTAMPTZ` | Yes | Last successful call |
| `last_error_at` | `TIMESTAMPTZ` | Yes | Last failed call |
| `last_error_message` | `TEXT` | Yes | Redacted error summary |
| `created_by_user_id` | `UUID` | No | FK `app_user.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, provider, status)`
2. Index on `(workspace_id, provider, status)`

### 4.7 `connector_sync_cursor`

Purpose: incremental sync position for pull-based connectors.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `connection_id` | `UUID` | No | FK `external_connection.id` |
| `cursor_name` | `VARCHAR(100)` | No | Logical stream, e.g. `issues`, `pages` |
| `cursor_value` | `TEXT` | Yes | Opaque upstream cursor |
| `cursor_state_json` | `JSONB` | Yes | Provider-specific incremental sync state |
| `last_synced_at` | `TIMESTAMPTZ` | Yes | Updated after successful sync |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(connection_id, cursor_name)`

### 4.8 `idempotency_key`

Purpose: webhook and command deduplication.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | Yes | Nullable for unauthenticated webhooks before resolution |
| `key` | `VARCHAR(255)` | No | Request idempotency key |
| `request_scope` | `VARCHAR(120)` | No | Endpoint or logical operation |
| `request_hash` | `VARCHAR(128)` | No | Hash of normalized request payload |
| `response_status` | `INTEGER` | Yes | Optional HTTP status |
| `result_ref` | `VARCHAR(255)` | Yes | Resource identifier or workflow id |
| `expires_at` | `TIMESTAMPTZ` | No | Retention deadline |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(request_scope, key)`

Indexes:

1. Index on `expires_at`

### 4.9 `workflow_run`

Purpose: root record for every graph execution.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | No | FK `workspace.id` |
| `workflow_type` | `VARCHAR(80)` | No | `auditflow_cycle`, `opsgraph_incident`, etc. |
| `subject_type` | `VARCHAR(80)` | No | Domain root type |
| `subject_id` | `UUID` | No | Domain root id |
| `status` | `VARCHAR(40)` | No | Workflow status enum |
| `current_state` | `VARCHAR(80)` | Yes | Current graph node/state |
| `requested_by_user_id` | `UUID` | Yes | Nullable for system/webhook start |
| `input_json` | `JSONB` | Yes | Start payload snapshot |
| `config_version` | `VARCHAR(50)` | No | Prompt/policy bundle version |
| `started_at` | `TIMESTAMPTZ` | Yes | Set when first node starts |
| `ended_at` | `TIMESTAMPTZ` | Yes | Set when terminal |
| `error_code` | `VARCHAR(80)` | Yes | Terminal or surfaced error code |
| `error_message` | `TEXT` | Yes | Redacted summary |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, workflow_type, status)`
2. Index on `(subject_type, subject_id, created_at DESC)`
3. Index on `(workspace_id, created_at DESC)`

### 4.10 `workflow_step_run`

Purpose: step-level execution for tracing, retries, and debugging.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workflow_run_id` | `UUID` | No | FK `workflow_run.id` |
| `step_name` | `VARCHAR(120)` | No | Graph node name |
| `attempt_number` | `INTEGER` | No | Starts at 1 |
| `status` | `VARCHAR(30)` | No | Step status enum |
| `worker_queue` | `VARCHAR(50)` | Yes | Celery queue if async |
| `model_name` | `VARCHAR(120)` | Yes | If model used |
| `token_input_count` | `INTEGER` | Yes | Optional |
| `token_output_count` | `INTEGER` | Yes | Optional |
| `latency_ms` | `INTEGER` | Yes | Execution latency |
| `input_ref_json` | `JSONB` | Yes | References to domain inputs, not full payload dump |
| `output_ref_json` | `JSONB` | Yes | References to artifacts or domain outputs |
| `error_code` | `VARCHAR(80)` | Yes | Failure class |
| `error_message` | `TEXT` | Yes | Redacted |
| `started_at` | `TIMESTAMPTZ` | Yes | Step start |
| `ended_at` | `TIMESTAMPTZ` | Yes | Step end |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(workflow_run_id, step_name, attempt_number)`
2. Index on `(organization_id, status, created_at DESC)`

### 4.11 `workflow_checkpoint`

Purpose: resumable graph state snapshots.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workflow_run_id` | `UUID` | No | FK `workflow_run.id` |
| `checkpoint_seq` | `INTEGER` | No | Monotonic within run |
| `state_name` | `VARCHAR(120)` | No | Graph state name |
| `checkpoint_json` | `JSONB` | No | Serialized state payload |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(workflow_run_id, checkpoint_seq)`

Indexes:

1. Index on `(workflow_run_id, checkpoint_seq DESC)`

### 4.12 `approval_task`

Purpose: human approval or review gate for workflow continuation.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | No | FK `workspace.id` |
| `workflow_run_id` | `UUID` | No | FK `workflow_run.id` |
| `subject_type` | `VARCHAR(80)` | No | Domain object type |
| `subject_id` | `UUID` | No | Domain object id |
| `approval_type` | `VARCHAR(50)` | No | `review_mapping`, `publish_comms`, etc. |
| `status` | `VARCHAR(30)` | No | Approval status enum |
| `policy_code` | `VARCHAR(80)` | No | Applied approval policy |
| `requested_by_user_id` | `UUID` | Yes | Nullable if system-created |
| `assigned_to_user_id` | `UUID` | Yes | Explicit approver if applicable |
| `decision_by_user_id` | `UUID` | Yes | Set on resolve |
| `decision_comment` | `TEXT` | Yes | Optional reviewer/approver note |
| `payload_json` | `JSONB` | Yes | Rendered summary and safe UI context |
| `requested_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `resolved_at` | `TIMESTAMPTZ` | Yes | Set when terminal |
| `expires_at` | `TIMESTAMPTZ` | Yes | Optional |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, status, requested_at DESC)`
2. Index on `(assigned_to_user_id, status, requested_at DESC)`
3. Index on `(workflow_run_id, status)`

### 4.13 `artifact`

Purpose: durable file/blob-like objects and generated outputs.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | Yes | FK `workspace.id` |
| `artifact_type` | `VARCHAR(40)` | No | Artifact type enum |
| `status` | `VARCHAR(30)` | No | Artifact status enum |
| `storage_bucket` | `VARCHAR(100)` | No | Physical object store bucket |
| `storage_key` | `TEXT` | No | Object path |
| `mime_type` | `VARCHAR(120)` | Yes | Optional content type |
| `byte_size` | `BIGINT` | Yes | Optional object size |
| `sha256` | `VARCHAR(64)` | Yes | Content hash |
| `display_name` | `VARCHAR(255)` | Yes | Friendly name |
| `provenance_json` | `JSONB` | No | Workflow run, source refs, snapshot refs |
| `metadata_json` | `JSONB` | Yes | Artifact-specific metadata |
| `created_by_user_id` | `UUID` | Yes | Nullable for worker-created |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | Soft delete only for mutable user uploads |

Indexes:

1. Index on `(organization_id, artifact_type, created_at DESC)`
2. Index on `(workspace_id, artifact_type, status)`
3. Index on `sha256`

### 4.14 `memory_record`

Purpose: durable structured memory facts and preferences.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | Yes | FK `workspace.id` |
| `scope` | `VARCHAR(40)` | No | Memory scope enum |
| `subject_type` | `VARCHAR(80)` | No | E.g. `service`, `control_family`, `incident` |
| `subject_id` | `UUID` | Yes | Nullable for org-wide facts |
| `memory_key` | `VARCHAR(120)` | No | Stable key for dedupe/update |
| `memory_type` | `VARCHAR(50)` | No | `fact`, `preference`, `pattern`, `glossary` |
| `value_json` | `JSONB` | No | Structured value |
| `confidence` | `NUMERIC(5,4)` | Yes | Optional confidence score |
| `source_kind` | `VARCHAR(40)` | No | `human_feedback`, `workflow_output`, `seeded` |
| `source_ref_json` | `JSONB` | Yes | Links to workflow, review, or artifact |
| `status` | `VARCHAR(30)` | No | `active`, `superseded`, `rejected` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(organization_id, scope, subject_type, subject_id, memory_key, status)` for `active` rows should be enforced logically in application/migration policy

Indexes:

1. Index on `(organization_id, scope, subject_type, subject_id)`
2. Index on `(memory_type, status)`

### 4.15 `embedding_chunk`

Purpose: semantic retrieval index entries.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | Yes | FK `workspace.id` |
| `subject_type` | `VARCHAR(80)` | No | `audit_evidence_chunk`, `incident_context`, etc. |
| `subject_id` | `UUID` | No | Domain record id |
| `chunk_index` | `INTEGER` | No | Order within subject |
| `text_content` | `TEXT` | No | Indexed text |
| `metadata_json` | `JSONB` | Yes | Search filters, offsets, source refs |
| `embedding_vector` | `VECTOR` | No | pgvector embedding column |
| `model_name` | `VARCHAR(120)` | No | Embedding model |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(subject_type, subject_id, chunk_index, model_name)`

Indexes:

1. Index on `(organization_id, subject_type, subject_id)`
2. HNSW or IVFFlat index on `embedding_vector`

### 4.16 `feedback_event`

Purpose: normalized human corrections and labels.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | Yes | FK `workspace.id` |
| `subject_type` | `VARCHAR(80)` | No | Domain object type |
| `subject_id` | `UUID` | No | Domain object id |
| `feedback_type` | `VARCHAR(50)` | No | `accept`, `reject`, `reassign`, `override`, etc. |
| `label_json` | `JSONB` | Yes | Structured correction payload |
| `comment` | `TEXT` | Yes | Human note |
| `actor_user_id` | `UUID` | No | FK `app_user.id` |
| `workflow_run_id` | `UUID` | Yes | If connected to a run |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, subject_type, subject_id, created_at DESC)`
2. Index on `(actor_user_id, created_at DESC)`

### 4.17 `audit_log`

Purpose: immutable security/compliance event trail.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | Yes | Nullable for pre-auth security events |
| `workspace_id` | `UUID` | Yes | Optional |
| `event_type` | `VARCHAR(80)` | No | E.g. `login_success`, `approval_resolved` |
| `subject_type` | `VARCHAR(80)` | Yes | Domain object type |
| `subject_id` | `UUID` | Yes | Domain object id |
| `actor_type` | `VARCHAR(30)` | No | `user`, `system`, `webhook` |
| `actor_id` | `UUID` | Yes | User id if actor is user |
| `request_id` | `VARCHAR(100)` | Yes | Correlates logs across services |
| `payload_json` | `JSONB` | Yes | Event payload, redacted |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, event_type, created_at DESC)`
2. Index on `(subject_type, subject_id, created_at DESC)`

### 4.18 `replay_case`

Purpose: saved, replayable workflow input case.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_id` | `UUID` | No | FK `workspace.id` |
| `workflow_type` | `VARCHAR(80)` | No | Same family as `workflow_run` |
| `subject_type` | `VARCHAR(80)` | No | Domain root type |
| `subject_id` | `UUID` | No | Domain root id |
| `case_name` | `VARCHAR(200)` | No | Human-friendly label |
| `input_snapshot_json` | `JSONB` | No | Deterministic replay input |
| `expected_output_json` | `JSONB` | Yes | Optional golden labels |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `created_by_user_id` | `UUID` | Yes | Nullable for automatic capture |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(organization_id, workflow_type, created_at DESC)`
2. Index on `(subject_type, subject_id)`

### 4.19 `replay_run`

Purpose: execution result of a replay case.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `replay_case_id` | `UUID` | No | FK `replay_case.id` |
| `status` | `VARCHAR(30)` | No | Replay status enum |
| `model_bundle_version` | `VARCHAR(80)` | No | Prompt/model config id |
| `score_json` | `JSONB` | Yes | Metric results |
| `output_snapshot_json` | `JSONB` | Yes | Redacted or sampled result snapshot |
| `started_at` | `TIMESTAMPTZ` | Yes | Start time |
| `ended_at` | `TIMESTAMPTZ` | Yes | End time |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(replay_case_id, created_at DESC)`
2. Index on `(organization_id, status, created_at DESC)`

### 4.20 `outbox_event`

Purpose: transactional outbox for background jobs and event publication.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | Yes | Nullable only for global/system events |
| `workspace_id` | `UUID` | Yes | Optional |
| `event_type` | `VARCHAR(120)` | No | Domain or platform event name |
| `aggregate_type` | `VARCHAR(80)` | No | Entity family |
| `aggregate_id` | `UUID` | No | Changed object id |
| `payload_json` | `JSONB` | No | Event payload |
| `status` | `VARCHAR(30)` | No | `pending`, `published`, `failed`, `discarded` |
| `available_at` | `TIMESTAMPTZ` | No | Retry scheduling |
| `published_at` | `TIMESTAMPTZ` | Yes | Set on success |
| `attempt_count` | `INTEGER` | No | Default `0` |
| `last_error_message` | `TEXT` | Yes | Redacted summary |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(status, available_at, created_at)`
2. Index on `(aggregate_type, aggregate_id, created_at DESC)`

## 5. Cross-Table Rules

### 5.1 Tenant Isolation

1. All tenant-scoped queries must filter by `organization_id`.
2. Cross-tenant joins are never allowed.
3. `workspace_id` is used for product scoping; it does not replace `organization_id`.

### 5.2 Artifact Provenance

1. Every generated artifact must include source `workflow_run_id`.
2. Artifacts generated from snapshots must include snapshot version in `provenance_json`.
3. Raw uploads should keep source filename and checksum in `metadata_json`.

### 5.3 Workflow Persistence

1. `workflow_run` is the source of truth for status.
2. `workflow_step_run` is append-only per attempt.
3. `workflow_checkpoint` stores resumable state only, not business truth.

### 5.4 Memory Lifecycle

1. Memory is never updated in place if semantic meaning changes materially; instead mark old row `superseded` and insert a new row.
2. Feedback-derived memory must store a `source_ref_json` pointer to the originating review or approval.

## 6. Query and Index Patterns

The following patterns must stay index-backed:

1. List workspaces for one organization
2. Fetch latest workflow runs for one subject
3. Fetch pending approval tasks for one user or organization
4. Retrieve artifacts by workspace and type
5. Retrieve memory by scope + subject
6. Poll pending outbox events
7. Run replay history by case and model bundle

## 7. Transaction Boundaries

### 7.1 Request-Side Writes

Single transaction:

1. Persist domain row changes
2. Persist corresponding `audit_log`
3. Persist any `outbox_event`

### 7.2 Approval Resolution

Single transaction:

1. Update `approval_task.status`
2. Update affected domain object status
3. Insert `feedback_event`
4. Insert `audit_log`
5. Insert `outbox_event`

### 7.3 Workflow Step Completion

Single transaction:

1. Update `workflow_step_run`
2. Update `workflow_run.current_state` and maybe `status`
3. Insert checkpoint if required
4. Persist domain changes for that step
5. Insert outbox events

## 8. ORM and Migration Notes

1. Model enums as PostgreSQL native enums only if migration discipline is strong; otherwise use constrained `VARCHAR`.
2. Prefer composite unique indexes over ORM-side dedupe assumptions.
3. Keep `embedding_vector` out of hot OLTP queries.
4. Create vector indexes only after initial backfill on large tables.
5. Large JSONB columns should not be eagerly loaded in default ORM queries.

## 9. Implementation Order

1. `organization`, `app_user`, `organization_membership`, `workspace`, `auth_session`
2. `external_connection`, `connector_sync_cursor`, `idempotency_key`
3. `workflow_run`, `workflow_step_run`, `workflow_checkpoint`, `approval_task`
4. `artifact`, `memory_record`, `embedding_chunk`, `feedback_event`
5. `audit_log`, `replay_case`, `replay_run`, `outbox_event`
