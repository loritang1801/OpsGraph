# OpsGraph Database Design

- Version: v0.1
- Date: 2026-03-16
- Scope: `OpsGraph` domain schema on top of the shared platform database

## 1. Database Overview

`OpsGraph` persists alert signals, incident state, context bundles, confirmed facts, hypotheses, runbook recommendations, communication drafts, timeline events, and postmortem outputs. Shared platform tables remain responsible for auth, workflow execution, approvals, artifacts, memory, and replay execution metadata.

### Physical Boundaries

1. Shared platform tables remain common across products.
2. `OpsGraph` tables use `incident`, `signal`, `service_`, and `postmortem` naming.
3. All domain rows except static configuration extensions carry `organization_id`.
4. Incident workflow data is optimized for append-heavy operations and fast current-state reads.

## 2. Domain Enumerations

### 2.1 Ops Workspace Status

- `active`
- `archived`

### 2.2 Service Status

- `active`
- `deprecated`
- `inactive`

### 2.3 Incident Severity

- `sev1`
- `sev2`
- `sev3`
- `sev4`

### 2.4 Incident Status

- `open`
- `investigating`
- `mitigating`
- `monitoring`
- `resolved`
- `closed`

### 2.5 Signal Source

- `prometheus`
- `grafana`
- `manual`

### 2.6 Signal Status

- `firing`
- `resolved`
- `suppressed`
- `duplicate`

### 2.7 Context Bundle Status

- `pending`
- `ready`
- `partial`
- `failed`

### 2.8 Fact Type

- `symptom`
- `impact`
- `change_event`
- `service_state`
- `root_cause`
- `mitigation`
- `resolution`

### 2.9 Fact Status

- `confirmed`
- `retracted`

### 2.10 Hypothesis Status

- `proposed`
- `accepted`
- `rejected`
- `stale`

### 2.11 Verification Step Status

- `pending`
- `completed`
- `skipped`

### 2.12 Recommendation Type

- `investigate`
- `mitigate`
- `rollback`
- `observe`
- `communicate`

### 2.13 Recommendation Status

- `proposed`
- `approved`
- `rejected`
- `executed`
- `skipped`

### 2.14 Recommendation Risk Level

- `read_only`
- `low_risk`
- `high_risk`

### 2.15 Communication Channel

- `internal_slack`
- `internal_feishu`
- `external_status_page`
- `incident_closure`

### 2.16 Communication Draft Status

- `draft`
- `approved`
- `published`
- `failed`
- `superseded`

### 2.17 Timeline Event Type

- `signal_received`
- `signal_resolved`
- `incident_created`
- `severity_changed`
- `fact_confirmed`
- `fact_retracted`
- `hypothesis_added`
- `hypothesis_resolved`
- `recommendation_approved`
- `message_published`
- `incident_resolved`
- `manual_note`

### 2.18 Postmortem Status

- `draft`
- `final`
- `superseded`

## 3. Core Tables

### 3.1 `ops_workspace`

Purpose: `OpsGraph` workspace extension over shared `workspace`.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `workspace_id` | `UUID` | No | PK, FK `workspace.id` |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `workspace_status` | `VARCHAR(30)` | No | Ops workspace status enum |
| `default_comms_channel` | `VARCHAR(40)` | Yes | Slack or Feishu preference |
| `incident_prefix` | `VARCHAR(40)` | Yes | Optional incident naming prefix |
| `settings_json` | `JSONB` | Yes | Severity policy, time windows, templates |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Unique on `workspace_id`
2. Index on `(organization_id, workspace_status)`

### 3.2 `service_registry`

Purpose: service inventory and ownership metadata.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `ops_workspace_id` | `UUID` | No | FK `ops_workspace.workspace_id` |
| `service_key` | `VARCHAR(120)` | No | Stable unique key within workspace |
| `display_name` | `VARCHAR(200)` | No | User-facing service name |
| `status` | `VARCHAR(30)` | No | Service status enum |
| `tier` | `VARCHAR(30)` | Yes | Optional criticality tier |
| `owner_user_id` | `UUID` | Yes | FK `app_user.id` |
| `primary_channel` | `VARCHAR(120)` | Yes | Slack/Feishu channel or group |
| `metadata_json` | `JSONB` | Yes | Tags, runtime, repo hints |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(ops_workspace_id, service_key)`

Indexes:

1. Index on `(organization_id, ops_workspace_id, status)`
2. Index on `(owner_user_id, status)`

### 3.3 `service_dependency`

Purpose: explicit service-to-service dependency graph.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `service_id` | `UUID` | No | FK `service_registry.id` |
| `depends_on_service_id` | `UUID` | No | FK `service_registry.id` |
| `dependency_type` | `VARCHAR(40)` | No | `sync_call`, `async_queue`, `database`, `cache` |
| `is_critical_path` | `BOOLEAN` | No | Default `false` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(service_id, depends_on_service_id, dependency_type)`

Indexes:

1. Index on `(service_id, is_critical_path)`
2. Index on `(depends_on_service_id)`

### 3.4 `incident`

Purpose: current-state incident record.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `ops_workspace_id` | `UUID` | No | FK `ops_workspace.workspace_id` |
| `service_id` | `UUID` | Yes | FK `service_registry.id` |
| `incident_key` | `VARCHAR(80)` | No | Human-friendly incident id |
| `title` | `VARCHAR(255)` | No | Active incident title |
| `severity` | `VARCHAR(20)` | No | Incident severity enum |
| `status` | `VARCHAR(30)` | No | Incident status enum |
| `current_commander_user_id` | `UUID` | Yes | FK `app_user.id` |
| `opened_at` | `TIMESTAMPTZ` | No | First detection time |
| `acknowledged_at` | `TIMESTAMPTZ` | Yes | First human ack |
| `resolved_at` | `TIMESTAMPTZ` | Yes | Resolution time |
| `closed_at` | `TIMESTAMPTZ` | Yes | Final close time |
| `dedupe_group_key` | `VARCHAR(255)` | Yes | Current grouping key |
| `current_fact_set_version` | `INTEGER` | No | Monotonic fact snapshot version |
| `current_context_bundle_id` | `UUID` | Yes | FK `context_bundle.id` |
| `summary_json` | `JSONB` | Yes | Cached UI summary |
| `created_by_signal_id` | `UUID` | Yes | FK `signal.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(ops_workspace_id, incident_key)`

Indexes:

1. Index on `(ops_workspace_id, status, severity, opened_at DESC)`
2. Index on `(service_id, status, severity, opened_at DESC)`
3. Index on `(organization_id, status, opened_at DESC)`

### 3.5 `signal`

Purpose: normalized alert or manually captured signal.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `ops_workspace_id` | `UUID` | No | FK `ops_workspace.workspace_id` |
| `service_id` | `UUID` | Yes | FK `service_registry.id`, derived if known |
| `source` | `VARCHAR(30)` | No | Signal source enum |
| `source_event_id` | `VARCHAR(255)` | Yes | Upstream alert id |
| `fingerprint` | `VARCHAR(128)` | No | Stable content hash |
| `dedupe_key` | `VARCHAR(255)` | No | Grouping key for clustering |
| `status` | `VARCHAR(30)` | No | Signal status enum |
| `title` | `VARCHAR(255)` | No | Normalized summary |
| `labels_json` | `JSONB` | Yes | Normalized labels |
| `annotations_json` | `JSONB` | Yes | Normalized annotations |
| `raw_payload_json` | `JSONB` | No | Upstream payload snapshot |
| `fired_at` | `TIMESTAMPTZ` | No | Alert firing time |
| `resolved_at` | `TIMESTAMPTZ` | Yes | Upstream resolve time |
| `received_at` | `TIMESTAMPTZ` | No | Ingest time |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(source, source_event_id)` when `source_event_id` is present

Indexes:

1. Index on `(ops_workspace_id, dedupe_key, fired_at DESC)`
2. Index on `(service_id, status, fired_at DESC)`
3. Index on `(fingerprint)`

### 3.6 `incident_signal_link`

Purpose: relation between incidents and signals.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `signal_id` | `UUID` | No | FK `signal.id` |
| `link_type` | `VARCHAR(30)` | No | `primary`, `supporting`, `duplicate` |
| `attached_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(incident_id, signal_id)`

Indexes:

1. Index on `(incident_id, attached_at DESC)`
2. Index on `(signal_id)`

### 3.7 `context_bundle`

Purpose: materialized incident context snapshot for a workflow step.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `bundle_status` | `VARCHAR(30)` | No | Context bundle status enum |
| `bundle_type` | `VARCHAR(30)` | No | `initial`, `refresh`, `post_resolution` |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `deployments_json` | `JSONB` | Yes | Recent deploys/commits |
| `tickets_json` | `JSONB` | Yes | Related Jira issues |
| `related_incidents_json` | `JSONB` | Yes | Similar incident refs |
| `runbook_refs_json` | `JSONB` | Yes | Resolved runbook references |
| `missing_sources_json` | `JSONB` | Yes | Connectors that failed or timed out |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(incident_id, created_at DESC)`
2. Index on `(organization_id, bundle_status, created_at DESC)`

### 3.8 `incident_fact`

Purpose: explicit confirmed fact set for the incident.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `fact_type` | `VARCHAR(40)` | No | Fact type enum |
| `status` | `VARCHAR(20)` | No | Fact status enum |
| `statement` | `TEXT` | No | Human-readable fact |
| `source_refs_json` | `JSONB` | No | Signals, deployments, tickets, chunks |
| `fact_set_version` | `INTEGER` | No | Monotonic version at insertion |
| `confirmed_by_user_id` | `UUID` | Yes | FK `app_user.id` |
| `created_from_hypothesis_id` | `UUID` | Yes | FK `hypothesis.id` |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(incident_id, fact_set_version, created_at)`
2. Index on `(incident_id, status, fact_type)`
3. Index on `(created_from_hypothesis_id)`

### 3.9 `hypothesis`

Purpose: candidate root-cause explanation separated from confirmed facts.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `status` | `VARCHAR(30)` | No | Hypothesis status enum |
| `rank` | `INTEGER` | No | Rank within incident at generation time |
| `confidence` | `NUMERIC(5,4)` | Yes | AI confidence |
| `title` | `VARCHAR(255)` | No | Short root-cause label |
| `rationale` | `TEXT` | Yes | Explanation |
| `evidence_refs_json` | `JSONB` | No | Supporting sources |
| `context_bundle_id` | `UUID` | Yes | FK `context_bundle.id` |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `superseded_by_id` | `UUID` | Yes | FK `hypothesis.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(incident_id, status, rank)`
2. Index on `(incident_id, created_at DESC)`

### 3.10 `verification_step`

Purpose: suggested validation steps for one hypothesis.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `hypothesis_id` | `UUID` | No | FK `hypothesis.id` |
| `step_order` | `INTEGER` | No | Ordered within hypothesis |
| `step_type` | `VARCHAR(40)` | No | `query`, `check`, `runbook`, `manual` |
| `instruction_text` | `TEXT` | No | Human-readable validation step |
| `expected_signal` | `TEXT` | Yes | What outcome to look for |
| `risk_level` | `VARCHAR(20)` | No | Recommendation risk level enum |
| `status` | `VARCHAR(20)` | No | Verification step status enum |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(hypothesis_id, step_order)`

Indexes:

1. Index on `(hypothesis_id, status, step_order)`

### 3.11 `runbook_recommendation`

Purpose: investigation or mitigation suggestion attached to an incident.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `hypothesis_id` | `UUID` | Yes | FK `hypothesis.id` |
| `recommendation_type` | `VARCHAR(30)` | No | Recommendation type enum |
| `risk_level` | `VARCHAR(20)` | No | Recommendation risk level enum |
| `status` | `VARCHAR(30)` | No | Recommendation status enum |
| `requires_approval` | `BOOLEAN` | No | Derived from risk level/policy |
| `approval_task_id` | `UUID` | Yes | FK `approval_task.id` |
| `title` | `VARCHAR(255)` | No | Short label |
| `instructions_markdown` | `TEXT` | No | Rendered action content |
| `source_refs_json` | `JSONB` | No | Backing evidence and runbooks |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(incident_id, status, risk_level, created_at DESC)`
2. Index on `(approval_task_id)`
3. Index on `(hypothesis_id, status)`

### 3.12 `comms_draft`

Purpose: communication draft tied to an incident fact set.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `channel_type` | `VARCHAR(40)` | No | Communication channel enum |
| `status` | `VARCHAR(30)` | No | Communication draft status enum |
| `fact_set_version` | `INTEGER` | No | Confirmed fact snapshot used |
| `approval_task_id` | `UUID` | Yes | FK `approval_task.id` |
| `body_markdown` | `TEXT` | No | Rendered draft |
| `structured_payload_json` | `JSONB` | Yes | Channel-specific message format |
| `published_message_ref` | `VARCHAR(255)` | Yes | Upstream message id |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `published_at` | `TIMESTAMPTZ` | Yes | Set once published |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Indexes:

1. Index on `(incident_id, channel_type, created_at DESC)`
2. Index on `(incident_id, status, channel_type)`
3. Index on `(approval_task_id)`

### 3.13 `timeline_event`

Purpose: append-only ordered incident history.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `event_index` | `BIGINT` | No | Monotonic per incident |
| `event_type` | `VARCHAR(40)` | No | Timeline event type enum |
| `occurred_at` | `TIMESTAMPTZ` | No | Event time |
| `actor_type` | `VARCHAR(30)` | No | `user`, `system`, `webhook` |
| `actor_id` | `UUID` | Yes | FK `app_user.id` if user |
| `subject_type` | `VARCHAR(80)` | Yes | Affected object type |
| `subject_id` | `UUID` | Yes | Affected object id |
| `payload_json` | `JSONB` | Yes | Event detail |
| `source_refs_json` | `JSONB` | Yes | Evidence/supporting refs |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(incident_id, event_index)`

Indexes:

1. Index on `(incident_id, event_index)`
2. Index on `(organization_id, event_type, occurred_at DESC)`

### 3.14 `postmortem`

Purpose: generated retrospective document for one incident.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | No | PK |
| `organization_id` | `UUID` | No | FK `organization.id` |
| `incident_id` | `UUID` | No | FK `incident.id` |
| `status` | `VARCHAR(30)` | No | Postmortem status enum |
| `fact_set_version` | `INTEGER` | No | Fact set backing this draft |
| `content_markdown` | `TEXT` | No | Full draft content |
| `follow_up_actions_json` | `JSONB` | Yes | Structured action items |
| `artifact_id` | `UUID` | Yes | FK `artifact.id` if exported |
| `replay_case_id` | `UUID` | Yes | FK `replay_case.id` |
| `source_workflow_run_id` | `UUID` | Yes | FK `workflow_run.id` |
| `finalized_by_user_id` | `UUID` | Yes | FK `app_user.id` |
| `finalized_at` | `TIMESTAMPTZ` | Yes | Finalization time |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Default `now()` |

Constraints:

1. Unique on `(incident_id, fact_set_version, status)` for active drafts/finals should be enforced logically

Indexes:

1. Index on `(incident_id, status, created_at DESC)`
2. Index on `(replay_case_id)`

## 4. Relationship Rules

### 4.1 Workspace, Service, Incident

1. One `ops_workspace` owns many `service_registry` rows.
2. One `service_registry` can have many incidents.
3. Incidents may exist without resolved `service_id` for early-stage ambiguous events.

### 4.2 Signal and Incident Correlation

1. `signal` is the normalized inbound unit.
2. `incident_signal_link` is the many-to-many join.
3. `incident.dedupe_group_key` stores the current grouping key used by the active incident.
4. Duplicate signals remain persisted; they are not dropped after correlation.

### 4.3 Facts, Hypotheses, and Recommendations

1. `hypothesis` is inferential and can never be treated as fact automatically.
2. `incident_fact` is the only source for confirmed facts.
3. `runbook_recommendation` may reference a hypothesis but is not itself proof.
4. Turning a hypothesis into a fact requires explicit user action or a guarded workflow transition.

### 4.4 Communication and Postmortem

1. Every `comms_draft` binds to one `fact_set_version`.
2. Publishing a draft does not mutate its fact set; later drafts must use newer versions.
3. `postmortem` also binds to a fixed `fact_set_version`.
4. A finalized postmortem can create or update a shared `replay_case`.

## 5. Query Patterns and Index Intent

These queries must remain index-backed:

1. Open incident list ordered by severity and opened time
2. Signals by dedupe key within a recent time window
3. Incident workspace load by incident id with latest facts, hypotheses, and recommendations
4. Recent unresolved recommendations requiring approval
5. Timeline fetch by incident ordered by `event_index`
6. Postmortem and replay history by incident

## 6. Consistency Rules

1. `incident.current_fact_set_version` increments whenever a new confirmed fact is inserted or an existing fact is retracted.
2. `comms_draft.fact_set_version` must be less than or equal to `incident.current_fact_set_version` and is immutable after insert.
3. `high_risk` `runbook_recommendation` rows must have `requires_approval = true`.
4. `timeline_event` is append-only and never updated after insert.
5. `incident.status = resolved` requires `resolved_at`.
6. `incident.status = closed` requires `resolved_at` and should only occur after postmortem kickoff or explicit manual closure.

## 7. Transaction Boundaries

### 7.1 Alert Intake

Single transaction:

1. Insert `signal`
2. Correlate or create `incident`
3. Insert `incident_signal_link`
4. Append `timeline_event`
5. Insert `outbox_event` for enrichment workflow

### 7.2 Fact Confirmation

Single transaction:

1. Increment `incident.current_fact_set_version`
2. Insert or retract `incident_fact`
3. Append `timeline_event`
4. Insert `feedback_event` if user-driven
5. Insert `outbox_event` for comms refresh if needed

### 7.3 Recommendation Approval

Single transaction:

1. Update `approval_task`
2. Update `runbook_recommendation.status`
3. Append `timeline_event`
4. Insert `audit_log`
5. Insert `outbox_event` for next workflow step

### 7.4 Communication Publish

Single transaction:

1. Update `comms_draft.status`
2. Store `published_message_ref`
3. Set `published_at`
4. Append `timeline_event`

### 7.5 Postmortem Finalization

Single transaction:

1. Update `postmortem.status` and `finalized_at`
2. Attach or create `replay_case`
3. Append `timeline_event`
4. Insert `outbox_event` for replay execution if enabled

## 8. Migration and ORM Notes

1. Keep `signal.raw_payload_json` and `context_bundle` JSONB columns lazily loaded in ORM models.
2. `event_index` should be assigned in the application transaction using an incident-scoped increment strategy.
3. `service_dependency` can be backfilled later without rewriting incident tables.
4. Use separate read queries for timeline and large context bundles to avoid oversized incident workspace joins.

## 9. Implementation Order

1. `ops_workspace`, `service_registry`, `service_dependency`
2. `incident`, `signal`, `incident_signal_link`
3. `context_bundle`, `incident_fact`, `hypothesis`, `verification_step`
4. `runbook_recommendation`, `comms_draft`, `timeline_event`
5. `postmortem`
