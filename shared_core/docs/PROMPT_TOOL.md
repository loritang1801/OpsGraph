# Shared Platform Prompt and Tool Contracts

- Version: v0.1
- Date: 2026-03-16
- Scope: Shared prompt bundle registry, model profiles, tool registry, adapter contracts, and agent invocation protocol for `AuditFlow` and `OpsGraph`

## 1. Contract Summary

This document defines the common prompt and tool protocol used by both products.

It standardizes:

1. How prompts are versioned and assembled
2. How model capabilities are expressed without binding to a provider
3. How logical tools map to adapters and external connections
4. How structured output, citations, and tool results are validated
5. How replay and evaluation bind to exact prompt and tool versions

It does not define domain-specific business logic, domain tool names, or final prompt wording.

## 2. Design Principles

1. Prompts are configuration artifacts, not inline strings embedded in node code.
2. Every specialist agent must use a fixed structured output schema.
3. Tool access is explicitly allowlisted per agent and per workflow node.
4. Only normalized tool results may re-enter prompt context; raw payloads remain behind references.
5. Any evidentiary or customer-visible claim must be grounded in citations or confirmed facts.
6. Replay must be able to reconstruct the exact prompt bundle, model profile, tool policy, and tool result references used by the original run.
7. The runtime remains provider-neutral and routes all model calls through the model gateway.

## 3. Shared Registry Model

### 3.1 Prompt Bundle Registry

Every agent invocation resolves one immutable prompt bundle from the registry.

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `bundle_id` | `string` | Yes | Stable prompt bundle name |
| `bundle_version` | `string` | Yes | Immutable version identifier |
| `workflow_type` | `string` | Yes | `auditflow_cycle` or `opsgraph_incident` |
| `agent_name` | `string` | Yes | Specialist agent identifier |
| `prompt_parts` | `object` | Yes | Templated prompt sections |
| `variable_contract` | `array` | Yes | Allowed variables and sources |
| `response_schema_ref` | `string` | Yes | Structured output schema id |
| `model_profile_id` | `string` | Yes | Logical model profile |
| `tool_policy_id` | `string` | Yes | Tool policy binding |
| `citation_policy_id` | `string` | Yes | Grounding and citation requirements |
| `context_budget_profile` | `string` | Yes | Token packing strategy |
| `status` | `string` | Yes | `active`, `shadow`, `deprecated` |

Example:

```json
{
  "bundle_id": "auditflow.mapper",
  "bundle_version": "2026-03-16.1",
  "workflow_type": "auditflow_cycle",
  "agent_name": "mapper_agent",
  "response_schema_ref": "auditflow.mapper.output.v1",
  "model_profile_id": "reasoning.standard",
  "tool_policy_id": "auditflow.mapper.policy.v1",
  "citation_policy_id": "evidence.required",
  "context_budget_profile": "long_context.reasoning.v1"
}
```

### 3.2 Model Profile Registry

Model profiles abstract runtime needs without hard-coding a vendor or model name into domain docs.

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `model_profile_id` | `string` | Yes | Stable logical profile name |
| `profile_kind` | `string` | Yes | `classification`, `extraction`, `reasoning`, `generation`, `summarization` |
| `capabilities` | `array[string]` | Yes | Required abilities such as `structured_output`, `tool_use`, `long_context` |
| `max_output_tokens` | `integer` | Yes | Safety guardrail for output size |
| `supports_tools` | `boolean` | Yes | Whether tool calls are allowed |
| `fallback_profile_id` | `string|null` | No | Lower-cost or lower-capability fallback |
| `timeout_ms` | `integer` | Yes | Default timeout |

### 3.3 Tool Policy Registry

Tool policy is resolved separately from the prompt bundle so runtime can enforce access even if prompt content drifts.

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `tool_policy_id` | `string` | Yes | Stable policy name |
| `tool_policy_version` | `string` | Yes | Immutable policy version |
| `agent_name` | `string` | Yes | Agent bound to the policy |
| `allowed_tools` | `array` | Yes | Tool allowlist with access mode |
| `max_tool_calls_per_turn` | `integer` | Yes | Loop guardrail |
| `allow_parallel_calls` | `boolean` | Yes | Whether runtime may fan out read-only calls |
| `degraded_mode_behavior` | `string` | Yes | `fail_closed`, `continue_partial`, or `human_gate` |

### 3.4 Tool Registry

The tool registry defines logical tools independent of external systems.

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `tool_name` | `string` | Yes | Stable logical tool id |
| `category` | `string` | Yes | `artifact`, `search`, `lookup`, `comms`, `approval`, `connector` |
| `access_mode` | `string` | Yes | `read_only`, `write`, `approval_required` |
| `input_schema_ref` | `string` | Yes | Input validation schema |
| `output_schema_ref` | `string` | Yes | Normalized output schema |
| `adapter_type` | `string` | Yes | Adapter implementation family |
| `idempotency_scope` | `string` | Yes | `none`, `request`, `subject`, or `publish` |
| `default_timeout_ms` | `integer` | Yes | Adapter timeout |
| `auth_context_source` | `string` | Yes | Where credentials/connection come from |

## 4. Prompt Bundle Contract

### 4.1 Prompt Assembly Order

Every invocation assembles prompt content in the following order:

1. `system_identity`
   Defines product role and hard safety boundaries
2. `developer_constraints`
   Defines workflow-specific rules, citation rules, and forbidden behaviors
3. `runtime_context`
   Injects current workflow state, authoritative status fields, and node purpose
4. `domain_context`
   Injects domain rows, retrieved evidence/facts, and policy snippets
5. `memory_context`
   Injects compressed long-term memory and prior accepted reviewer/operator patterns
6. `trigger_payload`
   Injects the current user action, webhook payload summary, or replay fixture
7. `tool_manifest`
   Injects allowed tool names, input hints, and access modes
8. `output_contract`
   Injects the exact required response schema and completion rules

The runtime must not reorder these sections.

### 4.2 Variable Contract

Each prompt variable must declare:

| Field | Meaning |
| --- | --- |
| `name` | Stable variable identifier |
| `required` | Whether prompt assembly fails without it |
| `source` | Workflow state, DB query, retrieval, memory, or trigger payload |
| `transform` | Redaction, truncation, normalization, or summarization |
| `max_tokens` | Packing budget for the field |
| `sensitivity` | `public`, `internal`, `restricted` |

Prompt assembly fails with `PROMPT_VARIABLE_MISSING` if a required variable cannot be resolved.

### 4.3 Context Packing Rules

Context packing follows these rules:

1. Preserve workflow identity, current node, schema contract, and hard safety instructions first.
2. Preserve authoritative rows and confirmed evidence/facts before summaries.
3. Drop low-priority memory before dropping primary domain evidence.
4. Never truncate JSON schema fragments or tool policy summaries mid-structure.
5. Long text must be chunked before prompt injection; prompts receive selected chunks plus stable refs.
6. Raw connector payloads are not injected directly unless the bundle explicitly permits a summarized form.

Default pruning order:

1. Advisory memory
2. Historical summaries
3. Low-rank retrieved chunks
4. Secondary policy prose
5. Any optional explanatory examples

### 4.4 Citation Policy

If a bundle is bound to a grounding-required citation policy:

1. Every evidentiary claim must return one or more citation refs.
2. Citations must point to stable internal refs such as `evidence_chunk`, `artifact`, `incident_fact`, `timeline_event`, or `deployment`.
3. A citation may include optional `locator` metadata such as page number, paragraph, or time range.
4. The runtime rejects output that references unknown or inaccessible refs.

Citation item shape:

```json
{
  "kind": "evidence_chunk",
  "id": "uuid",
  "locator": {
    "page": 3
  }
}
```

## 5. Model Profile Contracts

### 5.1 `classification`

Use for routing, severity suggestion, and label assignment.

Rules:

1. Output must be compact structured JSON.
2. Tool use is disabled unless the node explicitly enables read-only lookups.
3. Schema repair allows one retry before failing.

### 5.2 `extraction`

Use for field extraction from normalized text or artifacts.

Rules:

1. Favors deterministic field capture over long-form reasoning.
2. Must tolerate sparse input and partial extraction.
3. Citations are required when extracted fields drive later evidence claims.

### 5.3 `reasoning`

Use for ranked hypotheses, control mapping, and conflict analysis.

Rules:

1. Tool use is allowed only for allowlisted read-only tools unless the node explicitly supports approval creation.
2. Output must separate facts, hypotheses, and recommendations when relevant.
3. Rationale without citations does not count as grounded output.

### 5.4 `generation`

Use for narratives, summaries, and communication drafts.

Rules:

1. Must not introduce new facts absent from input context.
2. Must bind output to the snapshot or fact set version supplied in runtime context.
3. Tone and channel constraints are runtime variables, not hard-coded prompt text.

### 5.5 `summarization`

Use for memory compaction, replay notes, and context compression.

Rules:

1. Summaries must preserve source refs for later drill-down.
2. This profile cannot be used as a substitute for primary evidence in high-stakes outputs.

## 6. Shared Tool Contract

### 6.1 Tool Call Envelope

Every tool invocation uses the following envelope:

```json
{
  "tool_call_id": "uuid",
  "tool_name": "deployment.lookup",
  "workflow_run_id": "uuid",
  "subject_type": "incident",
  "subject_id": "uuid",
  "arguments": {},
  "idempotency_key": "workflow-run:node:step",
  "authorization_context": {
    "organization_id": "uuid",
    "workspace_id": "uuid",
    "connection_id": "uuid"
  }
}
```

### 6.2 Tool Result Envelope

Only this normalized result may be returned to the model:

```json
{
  "status": "success",
  "normalized_payload": {},
  "provenance": {
    "adapter_type": "github",
    "connection_id": "uuid",
    "fetched_at": "2026-03-16T09:00:00Z",
    "source_locator": "repo/deploy/123"
  },
  "raw_ref": {
    "artifact_id": "uuid",
    "kind": "external_payload"
  },
  "warnings": []
}
```

Rules:

1. `normalized_payload` is the only model-visible result body.
2. `raw_ref` points to persisted raw payload or snapshot for audit and replay.
3. Secrets, tokens, and connector-specific auth fields must never enter `normalized_payload`.
4. Tool results must be serializable and stable enough for replay fixtures.

### 6.3 Access Modes

`read_only`:

1. No external mutation
2. Safe for automatic retry
3. May run in parallel if policy allows

`write`:

1. Causes an external side effect
2. Requires an idempotency key
3. Must emit audit log and outbox event

`approval_required`:

1. May only be executed from a node contract that explicitly creates or consumes an approval gate
2. Runtime must reject direct execution in non-approved states
3. Approval decision id must be attached to the execution record

### 6.4 Adapter Mapping Rules

Logical tools map to adapters as follows:

1. The tool registry names the logical tool.
2. Runtime resolves the active external connection for the workspace or subject.
3. The adapter translates internal arguments into external API calls.
4. The adapter returns `normalized_payload`, `provenance`, `raw_ref`, and structured warnings/errors.
5. Domain nodes consume only the logical tool result, never adapter-specific response shapes.

### 6.5 Internal Interface Contracts

The runtime should expose at least these internal interfaces:

1. `PromptBundleRegistry.get(bundle_id, bundle_version)`
2. `ToolPolicyRegistry.get(tool_policy_id, tool_policy_version)`
3. `ModelProfileRegistry.get(model_profile_id)`
4. `ToolRegistry.get(tool_name)`
5. `ToolExecutor.execute(tool_call_envelope)`
6. `ModelGateway.invoke(agent_request)`

## 7. Agent Invocation Protocol

### 7.1 Invocation Steps

For every node-driven agent call:

1. Resolve the prompt bundle, model profile, and tool policy from versioned ids.
2. Resolve and transform required prompt variables.
3. Apply redaction and context packing.
4. Build the tool manifest from the effective tool policy.
5. Invoke the model gateway with the prompt parts and structured output schema.
6. Validate the returned JSON against the response schema.
7. Validate citations, version bindings, and tool policy compliance.
8. Persist trace metadata and any tool result refs before checkpointing node output.

### 7.2 Validation Loop

If structured output fails validation:

1. The runtime may issue one repair attempt with explicit validation errors.
2. If repair still fails, the node fails with `AGENT_OUTPUT_SCHEMA_INVALID`.
3. If citations are required but missing, fail with `CITATION_REQUIRED`.
4. If citations reference unknown refs, fail with `CITATION_REF_INVALID`.

### 7.3 Tool Loop Rules

1. The runtime, not the model, owns actual tool execution.
2. Tool calls beyond `max_tool_calls_per_turn` fail with `TOOL_CALL_LIMIT_EXCEEDED`.
3. Disallowed tools fail with `TOOL_POLICY_VIOLATION`.
4. Read-only tool timeout may degrade if the tool policy allows `continue_partial`.
5. Write and approval-required tools fail closed if preconditions are not satisfied.

## 8. Failure and Recovery Semantics

| Code | Retryable | Meaning |
| --- | --- | --- |
| `PROMPT_VARIABLE_MISSING` | No | Required variable could not be resolved |
| `PROMPT_ASSEMBLY_INVALID` | No | Prompt parts could not be assembled safely |
| `AGENT_OUTPUT_SCHEMA_INVALID` | No | Structured output could not be repaired |
| `CITATION_REQUIRED` | No | Required grounding missing |
| `CITATION_REF_INVALID` | No | Citation points to unknown/stale ref |
| `MODEL_PROFILE_UNAVAILABLE` | Yes | Runtime could not resolve backing model |
| `TOOL_POLICY_VIOLATION` | No | Agent requested forbidden tool/action |
| `TOOL_TIMEOUT` | Yes | Adapter timed out |
| `TOOL_AUTH_EXPIRED` | No | Connector credentials invalid |
| `TOOL_RATE_LIMITED` | Yes | External API throttled the request |

Recovery rules:

1. Retryable failures reuse the same prompt bundle and tool policy version.
2. Non-retryable failures must persist error context and stop node progression.
3. Human review or approval resumes must rehydrate current domain rows before any fresh model call.
4. If a bundle version is deprecated after a run starts, replay still uses the historical version recorded on the run.

## 9. Replay, Eval, and Version Binding

Every replayable agent execution must persist:

1. `bundle_id`
2. `bundle_version`
3. `tool_policy_id`
4. `tool_policy_version`
5. `model_profile_id`
6. Prompt variable hashes or serialized prompt parts
7. Tool call sequence and normalized results
8. Raw refs used for tool output provenance
9. Output schema ref

Replay rules:

1. Replay uses the same prompt bundle and tool policy version unless the replay case explicitly requests an override.
2. External write tools are stubbed during replay.
3. Read-only tools may use recorded fixtures instead of live adapters.
4. Eval compares structured output, warnings, and gating decisions, not just free-text summaries.

## 10. Mapping to Existing Docs

This document extends and must remain consistent with:

1. `WORKFLOW.md` for agent invocation lifecycle and node-level tool policy enforcement
2. `API.md` for approval, workflow, and artifact interfaces
3. `DATABASE.md` for versioned run metadata, artifacts, memory, and replay tables
