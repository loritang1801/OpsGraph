# Shared Platform Workflow Contracts

- Version: v0.1
- Date: 2026-03-16
- Scope: Shared workflow runtime, LangGraph state protocol, and specialist-agent contract for `AuditFlow` and `OpsGraph`

## 1. Contract Summary

This document defines the common execution protocol used by both products.

It standardizes:

1. The persisted workflow state envelope
2. Node input/output and checkpoint rules
3. Human gate behavior
4. Specialist agent invocation and output schema
5. Retry, recovery, replay, and compensation behavior

It does not define domain business logic. Domain-specific state and node semantics are documented separately.

## 2. Runtime Principles

1. Every workflow is resumable from a persisted checkpoint.
2. Every node must have explicit enter and exit conditions.
3. Every node must produce structured output, never free text as protocol.
4. Every external side effect must be traceable through database writes and outbox events.
5. Human interaction is modeled explicitly as a workflow pause, not as an implicit timeout.
6. A workflow may continue with partial data only when the node contract allows degraded mode.

## 3. Shared Workflow Model

### 3.1 Workflow Types

v1 supports at least:

1. `auditflow_cycle`
2. `opsgraph_incident`

### 3.2 Workflow Trigger Types

Every workflow run starts from one of these trigger classes:

1. `api_command`
   Started by explicit REST action
2. `webhook`
   Started by external signal delivery
3. `system_replay`
   Started by replay/eval infrastructure
4. `system_retry`
   Started by recovery scheduler or worker retry

### 3.3 Node Kinds

Every graph node must be categorized as one of:

1. `state_init`
2. `fan_out_ingest`
3. `analysis`
4. `human_input_gate`
5. `approval_gate`
6. `generation`
7. `publish_export`
8. `terminalize`

This category controls default retry behavior and whether the node may create `approval_task`.

## 4. Shared Workflow State Envelope

Every domain state object must embed the following shared envelope.

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `workflow_run_id` | `UUID` | Yes | Persistent run id |
| `organization_id` | `UUID` | Yes | Tenant boundary |
| `workspace_id` | `UUID` | Yes | Product workspace |
| `workflow_type` | `string` | Yes | Run family |
| `subject_type` | `string` | Yes | Domain root type |
| `subject_id` | `UUID` | Yes | Domain root id |
| `trigger_type` | `string` | Yes | Start trigger |
| `current_state` | `string` | Yes | Active graph state |
| `status` | `string` | Yes | Mirrors `workflow_run.status` |
| `run_config_version` | `string` | Yes | Prompt/policy/config bundle |
| `attempt_count` | `integer` | Yes | Current node attempt |
| `checkpoint_seq` | `integer` | Yes | Last persisted checkpoint seq |
| `pending_input_gate` | `object|null` | No | Domain input wait metadata |
| `pending_approval_gate` | `object|null` | No | Approval-task wait metadata |
| `artifact_refs` | `array` | Yes | Artifacts generated/consumed in run |
| `warning_codes` | `array[string]` | Yes | Non-fatal issues |
| `error_context` | `object|null` | No | Last node failure summary |
| `last_transition_at` | `timestamp` | Yes | Last successful state transition |

### 4.1 `pending_input_gate`

Used when the workflow must wait for a domain API mutation instead of a shared `approval_task`.

```json
{
  "gate_type": "domain_input",
  "reason_code": "review_queue_not_empty",
  "resume_api": "POST /api/v1/auditflow/mappings/:mappingId/review",
  "resume_condition": "no blocking mappings remain"
}
```

### 4.2 `pending_approval_gate`

Used when the workflow creates an `approval_task` and waits for explicit approval resolution.

```json
{
  "gate_type": "approval_task",
  "approval_task_ids": ["uuid"],
  "resume_policy": "all_approved",
  "rejection_policy": "return_to_previous_state"
}
```

### 4.3 `artifact_refs`

Every artifact reference inside workflow state must use:

```json
{
  "artifact_id": "uuid",
  "artifact_type": "export_package",
  "role": "generated_output"
}
```

## 5. Shared Node Contract Template

Every workflow node definition must specify the following fields.

| Field | Meaning |
| --- | --- |
| `node_name` | Stable graph node identifier |
| `node_kind` | One of the shared node kinds |
| `enter_conditions` | Preconditions to enter node |
| `state_inputs` | Required fields from workflow state |
| `db_reads` | Tables/entities read |
| `db_writes` | Tables/entities written |
| `tools_used` | Shared adapters or domain tools allowed |
| `agent_used` | Specialist agent or `none` |
| `output_patch` | Fields written back to workflow state |
| `events_emitted` | Outbox events or SSE updates produced |
| `checkpoint_policy` | When checkpoint is persisted |
| `retry_policy` | Retryable vs terminal failures |
| `exit_conditions` | Conditions to leave node |
| `next_state_rule` | Deterministic next-state logic |

### 5.1 Node Output Patch Rule

Nodes do not replace the whole workflow state. They return an output patch:

```json
{
  "current_state": "mapping",
  "warning_codes": ["PARTIAL_SOURCE_FAILURE"],
  "error_context": null
}
```

The runtime merges this patch into persisted state before checkpointing.

## 6. Shared Human Gate Protocol

There are two supported gate types in v1.

### 6.1 `human_input_gate`

Use when:

1. Domain review is naturally done through domain APIs
2. No shared approval inbox is needed
3. Resume condition is based on domain object state

Behavior:

1. Set `workflow_run.status = waiting_for_input`
2. Persist `pending_input_gate`
3. Emit domain event/SSE update
4. Resume when a subscribed domain mutation satisfies the gate condition

Examples:

1. AuditFlow reviewer queue resolution
2. OpsGraph severity confirmation
3. OpsGraph incident resolve input

### 6.2 `approval_gate`

Use when:

1. Action requires explicit approval tracking
2. UI should surface an approval inbox item
3. Resume condition is approval resolution

Behavior:

1. Create one or more `approval_task` rows
2. Set `workflow_run.status = waiting_for_approval`
3. Persist `pending_approval_gate`
4. Resume when approval policy is satisfied

Examples:

1. High-risk recommendation approval
2. External communication publish approval

### 6.3 Gate Resume Semantics

All gate resumes must:

1. Rehydrate latest workflow state from checkpoint
2. Re-read authoritative domain rows from DB
3. Re-evaluate stale conditions before continuing
4. Increment `attempt_count` for the resumed node

## 7. Specialist Agent Base Contract

### 7.1 Shared Agent Input Envelope

Every agent invocation receives:

```json
{
  "agent_name": "mapper_agent",
  "workflow_run_id": "uuid",
  "organization_id": "uuid",
  "workspace_id": "uuid",
  "subject_type": "audit_cycle",
  "subject_id": "uuid",
  "context": {},
  "policies": {},
  "memory_context": [],
  "tool_permissions": [
    {
      "tool_name": "jira.read_issue",
      "mode": "read_only"
    }
  ]
}
```

### 7.2 Shared Agent Output Envelope

Every agent must return:

```json
{
  "status": "success",
  "summary": "Short execution summary.",
  "structured_output": {},
  "citations": [],
  "warnings": [],
  "needs_human_input": false
}
```

Rules:

1. `structured_output` is mandatory
2. `citations` is mandatory when agent makes evidentiary claims
3. `summary` is human-readable but not used as protocol input
4. If schema validation fails, treat as non-retryable model error

### 7.3 Shared Citation Shape

```json
{
  "kind": "evidence_chunk",
  "id": "uuid",
  "locator": {
    "page": 2,
    "char_start": 10,
    "char_end": 120
  }
}
```

### 7.4 Tool Use Rules

1. Agent tools must be explicitly whitelisted per node
2. `approval_required` tools cannot be called inside a node unless that node is specifically defined to create an approval gate
3. If an agent requests a disallowed tool, the node fails with `TOOL_POLICY_VIOLATION`

## 8. Shared Retry and Failure Semantics

### 8.1 Failure Classes

| Class | Retryable | Meaning |
| --- | --- | --- |
| `CONNECTOR_TIMEOUT` | Yes | Upstream network/connectivity issue |
| `CONNECTOR_AUTH_EXPIRED` | No | Human re-auth required |
| `MODEL_PROVIDER_TRANSIENT` | Yes | Temporary model outage |
| `MODEL_SCHEMA_VIOLATION` | No | Structured output contract broken |
| `STALE_DOMAIN_STATE` | No | Client/domain state changed since node input |
| `PARTIAL_DATA_UNAVAILABLE` | Contextual | Node may continue in degraded mode |
| `TOOL_POLICY_VIOLATION` | No | Disallowed tool attempt |
| `FATAL_PRECONDITION_FAILED` | No | Enter condition invalid |

### 8.2 Retry Defaults by Node Kind

1. `state_init`
   No automatic retry on precondition failure
2. `fan_out_ingest`
   Retry retryable child jobs; do not rerun successful children
3. `analysis`
   Retry transient connector/model failures up to bounded limit
4. `human_input_gate`
   No retry; wait for external state change
5. `approval_gate`
   No retry; wait for approval resolution
6. `generation`
   Retry transient model failures only
7. `publish_export`
   Retry transport/storage errors; not stale-state violations
8. `terminalize`
   Retry only if final side effect failed before durable write

### 8.3 Checkpoint Policy

A checkpoint must be written:

1. Before entering any human gate
2. After any node that writes domain rows
3. Before any publish/export side effect
4. After any node that emits terminal or customer-visible artifacts

## 9. Shared Compensation and Recovery

### 9.1 No Destructive Rollback

v1 does not roll back successful domain writes. Compensation is forward-only.

Examples:

1. Mark draft as `failed` instead of deleting it
2. Insert superseding mapping/hypothesis instead of overwriting prior history
3. Retry export build without mutating approved snapshot

### 9.2 Worker Crash Recovery

On worker restart:

1. Find `workflow_run.status in (running, waiting_for_input, waiting_for_approval)`
2. Rehydrate latest checkpoint
3. Inspect `workflow_step_run` attempt status
4. Resume only if node side effects are idempotent or incomplete

### 9.3 Stale-State Recovery

If node resumes against stale domain state:

1. Mark `error_context.code = STALE_DOMAIN_STATE`
2. Recompute gate conditions or reroute to previous stable state
3. Never continue using cached fact or snapshot assumptions

## 10. Shared Replay Contract

### 10.1 Replay Input

Replay must capture:

1. Workflow trigger payload
2. Domain root snapshot identifiers
3. Referenced artifact ids
4. Config/prompt/model bundle version

### 10.2 Replay Execution Rules

1. No live external side effects
2. Connector reads come from recorded snapshots or mocks
3. Approval and input gates are auto-resolved from replay fixture decisions
4. Replay uses the same structured node schemas as production

### 10.3 Replay Output

Replay must record:

1. Final workflow terminal state
2. Node-by-node outcome
3. Structured outputs for scored nodes
4. Error class if failed

## 11. Shared Test Scenarios

Every workflow implementation must have:

1. One happy-path end-to-end run
2. One transient connector failure recovery test
3. One model schema violation test
4. One human input gate pause/resume test
5. One approval gate pause/resume test
6. One stale-state resume test
7. One replay execution test

## 12. Mapping to Shared Tables and APIs

1. Workflow runtime state persists to `workflow_run`, `workflow_checkpoint`, `workflow_step_run`
2. Approval gates persist to `approval_task`
3. Domain input gates are resumed by domain APIs and tracked in workflow state only
4. Generated outputs persist to `artifact`
5. Human corrections persist to `feedback_event`
6. All node side effects publish through `outbox_event`
