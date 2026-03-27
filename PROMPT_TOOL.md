# OpsGraph Prompt and Tool Contracts

- Version: v0.1
- Date: 2026-03-16
- Scope: `OpsGraph` prompt bundle templates, agent-specific tool contracts, adapter mappings, and fact-bound generation rules

## 1. Contract Summary

This document defines the prompt and tool contracts used by `OpsGraph` specialist agents.

It locks:

1. Prompt bundle ids and template structure per incident agent
2. Required variables and forbidden context for facts, hypotheses, recommendations, and comms
3. Logical tool names, output normalization, and adapter mappings
4. Approval and stale fact set boundaries
5. Failure handling for partial enrichment, unsupported claims, and unsafe recommendation output

The contracts in this document extend the shared rules in `SharedAgentCore/docs/PROMPT_TOOL.md`.

## 2. Bundle Registry

| Agent | Bundle Id | Default Model Profile | Primary Nodes | Response Schema Ref |
| --- | --- | --- | --- | --- |
| `triage_agent` | `opsgraph.triage` | `classification.standard` | `triage` | `opsgraph.triage.output.v1` |
| `investigator_agent` | `opsgraph.investigator` | `reasoning.standard` | `hypothesize` | `opsgraph.investigator.output.v1` |
| `runbook_advisor` | `opsgraph.runbook_advisor` | `reasoning.standard` | `advise` | `opsgraph.runbook_advisor.output.v1` |
| `comms_agent` | `opsgraph.comms` | `generation.grounded` | `communicate` | `opsgraph.comms.output.v1` |
| `postmortem_reviewer` | `opsgraph.postmortem_reviewer` | `generation.grounded` | `retrospective` | `opsgraph.postmortem.output.v1` |

Every bundle version must be recorded on the workflow run before the agent executes.

## 3. Shared OpsGraph Prompt Context

### 3.1 Authoritative Context Sources

OpsGraph bundles may draw from:

1. `IncidentWorkflowState`
2. `incident`, `signal`, `context_bundle`, and `timeline_event`
3. Confirmed `incident_fact` rows and current `current_fact_set_version`
4. Ranked `hypothesis` rows and `verification_step`
5. `runbook_recommendation`, approval state, and comms channel policy
6. Service memory such as prior incidents, runbooks, owners, and recent deploy metadata

### 3.2 Forbidden Default Context

The runtime must not inject the following into prompts unless the bundle explicitly needs them for analysis:

1. Unconfirmed hypotheses into external communication generation
2. Rejected recommendations as active operator guidance
3. Private approval comments into customer-visible comms
4. Facts from a different fact set version than the one bound to the current node

### 3.3 Context Packing Priority

When token limits force pruning, keep context in this order:

1. Incident identity, severity, and current status
2. Confirmed facts for the active fact set version
3. Latest correlated signals and recent deploy/change context
4. Service topology and runbook references
5. Historical summaries and long-tail incident memory

## 4. Agent Prompt Templates

### 4.1 `triage_agent`

Purpose:

1. Cluster inbound signals
2. Suggest incident severity and likely service ownership
3. Produce an initial blast-radius summary

Prompt parts:

1. `system_identity`: incident triage classifier
2. `developer_constraints`: output a recommendation, not a final declaration when confidence is low
3. `runtime_context`: signal ids, current incident ids if correlated, environment metadata
4. `domain_context`: normalized signals, service catalog candidates, recent incident collisions
5. `tool_manifest`
6. `output_contract`

Required variables:

1. `signal_ids`
2. `signal_summaries`
3. `environment_name`
4. `current_incident_candidates`

Forbidden context:

1. Hypotheses not yet generated
2. Recommendation execution history
3. Customer-visible comms drafts

Allowed tools:

1. `signal.read`
2. `service_registry.lookup`

Output contract:

```json
{
  "dedupe_group_key": "checkout-api:high-error-rate",
  "severity": "sev1",
  "severity_confidence": 0.82,
  "title": "Elevated 5xx on checkout-api",
  "service_id": "uuid",
  "blast_radius_summary": "Checkout traffic is impacted across the primary region."
}
```

Rules:

1. If `severity_confidence` is below the node threshold, the workflow must open a domain `human_input_gate`.
2. `triage_agent` may suggest incident grouping, but the node owns final dedupe persistence logic.

### 4.2 `investigator_agent`

Purpose:

1. Analyze current incident context
2. Produce ranked hypotheses
3. Provide explicit verification steps without converting them into facts

Prompt parts:

1. `system_identity`: incident investigator separating facts from hypotheses
2. `developer_constraints`: never label a hypothesis as a confirmed fact
3. `runtime_context`: incident id, severity, missing source warnings
4. `domain_context`: context bundle summary, confirmed facts, recent deploys, service dependencies, signal patterns
5. `memory_context`: recent incident patterns for same service
6. `tool_manifest`
7. `output_contract`

Required variables:

1. `incident_id`
2. `context_bundle_id`
3. `current_fact_set_version`
4. `confirmed_fact_refs`
5. `context_missing_sources`

Forbidden context:

1. Approval comments
2. External comms content
3. Proposed but unpersisted recommendations

Allowed tools:

1. `signal.read`
2. `incident.read_timeline`
3. `context_bundle.read`
4. `deployment.lookup`
5. `service_registry.lookup`

Output contract:

```json
{
  "hypotheses": [
    {
      "title": "Recent deploy introduced connection pool exhaustion.",
      "confidence": 0.78,
      "rank": 1,
      "evidence_refs": [
        {
          "kind": "deployment",
          "id": "deploy-123"
        }
      ],
      "verification_steps": [
        {
          "step_order": 1,
          "instruction_text": "Check DB connection saturation metrics."
        }
      ]
    }
  ]
}
```

Rules:

1. `evidence_refs` may point only to authoritative refs already present in storage or normalized tool results.
2. Verification steps must be operational suggestions, not implied facts.
3. Missing enrichment sources must be acknowledged in warnings or confidence scoring.

### 4.3 `runbook_advisor`

Purpose:

1. Recommend mitigations or diagnostic actions
2. Classify risk and approval needs
3. Keep recommendation content distinct from incident facts

Prompt parts:

1. `system_identity`: runbook-based incident advisor
2. `developer_constraints`: do not execute actions; classify risk conservatively
3. `runtime_context`: incident id, severity, approved mitigation policy
4. `domain_context`: confirmed facts, top hypotheses, relevant runbooks, deployment context
5. `memory_context`: prior successful mitigations for same service
6. `tool_manifest`
7. `output_contract`

Required variables:

1. `incident_id`
2. `current_fact_set_version`
3. `confirmed_fact_refs`
4. `top_hypothesis_refs`
5. `service_id`

Forbidden context:

1. Pending comms drafts
2. Future resolve decisions
3. Unverified claims from operator chat

Allowed tools:

1. `context_bundle.read`
2. `deployment.lookup`
3. `service_registry.lookup`
4. `runbook.search`
5. `approval_task.read_state`

Output contract:

```json
{
  "recommendations": [
    {
      "recommendation_type": "mitigate",
      "risk_level": "high_risk",
      "requires_approval": true,
      "title": "Roll back deployment 123",
      "instructions_markdown": "Revert checkout-api deploy 123.",
      "evidence_refs": [
        {
          "kind": "deployment",
          "id": "deploy-123"
        }
      ]
    }
  ]
}
```

Rules:

1. Every recommendation must have an explicit `risk_level`.
2. High-risk recommendations must set `requires_approval = true`.
3. Missing `evidence_refs` makes the recommendation invalid.
4. The tool contract does not permit external execution; this agent only proposes actions.

### 4.4 `comms_agent`

Purpose:

1. Generate internal and external communication drafts
2. Bind every draft to one exact fact set version
3. Enforce channel policy and claim boundaries

Prompt parts:

1. `system_identity`: grounded incident communications writer
2. `developer_constraints`: use confirmed facts only; do not speculate; match channel policy
3. `runtime_context`: incident id, severity, `current_fact_set_version`, target channel, publish policy
4. `domain_context`: confirmed facts, public-safe service names, timeline excerpts, operator-approved wording preferences
5. `tool_manifest`
6. `output_contract`

Required variables:

1. `incident_id`
2. `current_fact_set_version`
3. `confirmed_fact_refs`
4. `target_channels`
5. `channel_policy`

Forbidden context:

1. Unconfirmed hypotheses
2. Rejected or pending recommendations unless they are explicitly transformed into confirmed status facts
3. Internal-only root cause speculation for external channels

Allowed tools:

1. `incident.read_timeline`
2. `context_bundle.read`
3. `comms.channel_preview`
4. `approval_task.read_state`

Output contract:

```json
{
  "drafts": [
    {
      "channel_type": "internal_slack",
      "fact_set_version": 3,
      "body_markdown": "We are investigating elevated error rates affecting checkout.",
      "fact_refs": [
        {
          "kind": "incident_fact",
          "id": "uuid"
        }
      ]
    }
  ]
}
```

Rules:

1. Each draft must echo the bound `fact_set_version`.
2. External drafts may reference only facts approved for external visibility.
3. If `fact_set_version` changes before publish, the draft becomes stale and must not be published.

### 4.5 `postmortem_reviewer`

Purpose:

1. Generate a grounded retrospective
2. Suggest follow-up actions
3. Provide replay capture hints

Prompt parts:

1. `system_identity`: incident postmortem writer using confirmed timeline and facts
2. `developer_constraints`: separate timeline, contributing factors, and action items; do not invent missing causality
3. `runtime_context`: incident id, resolution status, final fact set version
4. `domain_context`: confirmed facts, timeline events, final recommendations, deploy/change refs
5. `memory_context`: prior postmortem style preferences for the organization
6. `tool_manifest`
7. `output_contract`

Required variables:

1. `incident_id`
2. `current_fact_set_version`
3. `confirmed_fact_refs`
4. `timeline_refs`
5. `resolution_summary`

Forbidden context:

1. Abandoned hypotheses presented as root cause
2. Draft comms content that diverges from final facts

Allowed tools:

1. `incident.read_timeline`
2. `context_bundle.read`
3. `deployment.lookup`
4. `service_registry.lookup`

Output contract:

```json
{
  "postmortem_markdown": "At 09:00 UTC, checkout-api began returning elevated 5xx responses after deployment 123.",
  "follow_up_actions": [
    {
      "title": "Add connection pool saturation alert",
      "owner_hint": "payments-sre"
    }
  ],
  "replay_capture_hints": [
    "include deployment 123 metadata",
    "include DB saturation dashboard snapshot"
  ]
}
```

Rules:

1. Postmortem text must reflect confirmed facts only.
2. Follow-up actions may be inferred from confirmed issues and accepted recommendations.
3. Replay hints must reference available artifacts or timeline refs, not hypothetical future data.

## 5. OpsGraph Tool Registry

### 5.1 Logical Tools

| Tool | Access | Purpose | Normalized Output |
| --- | --- | --- | --- |
| `signal.read` | `read_only` | Read normalized signal rows and correlation metadata | Signal summaries, timestamps, correlation keys |
| `incident.read_timeline` | `read_only` | Read incident timeline events | Ordered timeline entries with visibility flags |
| `context_bundle.read` | `read_only` | Read the current context bundle snapshot | Context summary, missing sources, refs |
| `deployment.lookup` | `read_only` | Read recent deployment/change metadata | Deploy ids, commit refs, actor, timestamps |
| `service_registry.lookup` | `read_only` | Read service ownership and dependency metadata | Owner refs, dependency summaries, runbook links |
| `runbook.search` | `read_only` | Search runbooks by service or symptom | Ranked runbook refs and excerpts |
| `comms.channel_preview` | `read_only` | Validate draft formatting and channel constraints before publish | Preview body, channel limits, policy warnings |
| `approval_task.read_state` | `read_only` | Read approval status for recommendation/comms flow | Approval ids, status, resolved_at |

### 5.2 Adapter Mapping

| Tool | Primary Adapter | Notes |
| --- | --- | --- |
| `signal.read` | OpsGraph database over normalized signals | Prometheus/Grafana webhook payloads are normalized before agent access |
| `incident.read_timeline` | OpsGraph database | Includes visibility flags for internal vs external use |
| `context_bundle.read` | Context bundle reader | Wraps stored enrichment snapshot |
| `deployment.lookup` | GitHub adapter or remote HTTP provider | Exposes normalized deploy/change metadata only; falls back to local heuristics in `auto` mode |
| `service_registry.lookup` | Service registry database or remote HTTP provider | Read-only service metadata and ownership; canonical contract lives in `INTEGRATIONS.md` |
| `runbook.search` | Vector search adapter or remote HTTP provider | Returns runbook refs, not raw full documents by default; remote contract also documented in `INTEGRATIONS.md` |
| `comms.channel_preview` | Slack/Feishu policy adapter | Preview-only, no publish side effect |
| `approval_task.read_state` | Shared platform approval store | Read-only approval status bridge |

Connector notes:

1. Webhook signature validation and normalization happen before agent invocation.
2. Slack/Feishu publish remains a separate workflow action, not a general tool call in v1.

## 6. Tool Policies and Guardrails

1. `triage_agent` must stay read-only and may not trigger approval or publish side effects.
2. `investigator_agent` may read context and deploy metadata but may not search comms channels or approval state.
3. `runbook_advisor` may read approval state for context but cannot execute runbooks.
4. `comms_agent` may preview channel rendering but cannot publish directly.
5. `postmortem_reviewer` may read only final timeline and confirmed fact context.

## 7. Failure and Recovery Rules

1. If `current_fact_set_version` changes after prompt assembly, all generated comms drafts are stale and must be discarded.
2. If `comms_agent` output references anything other than confirmed facts, fail with `FACT_SET_POLICY_VIOLATION`.
3. If `runbook_advisor` omits `risk_level` or `evidence_refs`, fail with `AGENT_OUTPUT_SCHEMA_INVALID`.
4. If enrichment tools partially fail, `investigator_agent` may continue only if missing sources are injected as warnings.
5. If `comms.channel_preview` times out, internal draft generation may continue, but publish readiness must remain false.

## 8. Replay and Version Binding

Each OpsGraph agent run must persist:

1. `bundle_id` and `bundle_version`
2. `response_schema_ref`
3. `tool_policy_id` and `tool_policy_version`
4. `current_fact_set_version` for `investigator_agent`, `runbook_advisor`, `comms_agent`, and `postmortem_reviewer`
5. Context bundle refs and deploy/runbook refs used in prompt assembly
6. Tool result `raw_ref` values for replay fixtures

Replay rules:

1. `comms_agent` replay must use the same `current_fact_set_version` or explicitly mark the case as stale-comparison replay.
2. External publish side effects remain stubbed.
3. Signal and deployment fixtures may be replayed from normalized payload snapshots instead of live connectors.

## 9. Mapping to Existing Docs

This document extends and must remain consistent with:

1. `WORKFLOW.md` for OpsGraph node sequencing, gates, and stale fact set behavior
2. `API.md` for signal intake, incident workspace, recommendation approval, and comms publish interfaces
3. `DATABASE.md` for signal, incident, fact, hypothesis, recommendation, comms draft, and postmortem tables
4. `D:/project/SharedAgentCore/docs/PROMPT_TOOL.md` for shared registry and runtime protocol
