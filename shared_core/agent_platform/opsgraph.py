# noqa: PLR0915
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .shared import (
    PromptBundle,
    PromptPartSpec,
    PromptVariableContract,
    RuntimeCatalog,
    SchemaModel,
    SharedWorkflowStateEnvelope,
    ToolDefinition,
    ToolPolicy,
    ToolPolicyEntry,
)

BUNDLE_VERSION = "2026-03-16.1"
TOOL_VERSION = "2026-03-16.1"
TOOL_POLICY_VERSION = "2026-03-16.1"


def _part(
    name: PromptPartSpec.__annotations__["name"],
    description: str,
    required_variables: list[str] | None = None,
    instructions: list[str] | None = None,
) -> PromptPartSpec:
    return PromptPartSpec(
        name=name,
        description=description,
        required_variables=required_variables or [],
        instructions=instructions or [],
    )


def _var(
    name: str,
    source: PromptVariableContract.__annotations__["source"],
    *,
    required: bool = True,
    transform: list[str] | None = None,
    max_tokens: int | None = None,
    sensitivity: Literal["public", "internal", "restricted"] = "internal",
) -> PromptVariableContract:
    return PromptVariableContract(
        name=name,
        required=required,
        source=source,
        transform=transform or [],
        max_tokens=max_tokens,
        sensitivity=sensitivity,
    )


def _tool_entry(tool_name: str) -> ToolPolicyEntry:
    return ToolPolicyEntry(tool_name=tool_name, tool_version=TOOL_VERSION, access_mode="read_only")


class FactGate(SchemaModel):
    required_fact_set_version: int
    current_fact_set_version: int
    is_satisfied: bool


class IncidentWorkflowState(SharedWorkflowStateEnvelope):
    workflow_type: Literal["opsgraph_incident"] = "opsgraph_incident"
    subject_type: Literal["incident"] = "incident"
    current_state: str = "detect"
    ops_workspace_id: str
    incident_id: str
    service_id: str | None = None
    incident_status: str
    severity: str
    severity_confidence: float | None = None
    signal_ids: list[str] = Field(default_factory=list)
    context_bundle_id: str | None = None
    context_missing_sources: list[str] = Field(default_factory=list)
    investigation_memory_context: list[dict[str, Any]] = Field(default_factory=list)
    recommendation_memory_context: list[dict[str, Any]] = Field(default_factory=list)
    postmortem_memory_context: list[dict[str, Any]] = Field(default_factory=list)
    current_fact_set_version: int
    confirmed_fact_ids: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    top_hypothesis_ids: list[str] = Field(default_factory=list)
    recommendation_ids: list[str] = Field(default_factory=list)
    pending_approval_task_ids: list[str] = Field(default_factory=list)
    comms_draft_ids: list[str] = Field(default_factory=list)
    publish_ready_draft_ids: list[str] = Field(default_factory=list)
    postmortem_id: str | None = None
    replay_case_id: str | None = None
    resolve_requested: bool = False


class SignalReadArgs(SchemaModel):
    signal_ids: list[str] = Field(min_length=1)


class SignalSummary(SchemaModel):
    signal_id: str
    source: str
    correlation_key: str
    summary: str
    observed_at: datetime


class SignalReadResult(SchemaModel):
    signals: list[SignalSummary] = Field(default_factory=list)


class IncidentReadTimelineArgs(SchemaModel):
    incident_id: str
    limit: int = 50
    visibility: Literal["internal", "external", "all"] = "all"


class TimelineEntry(SchemaModel):
    timeline_event_id: str
    event_type: str
    created_at: datetime
    summary: str
    visibility: Literal["internal", "external"]


class IncidentReadTimelineResult(SchemaModel):
    timeline: list[TimelineEntry] = Field(default_factory=list)


class ContextBundleReadArgs(SchemaModel):
    incident_id: str
    context_bundle_id: str | None = None


class ContextBundleReadResult(SchemaModel):
    context_bundle_id: str
    summary: str
    missing_sources: list[str] = Field(default_factory=list)
    refs: list[dict[str, Any]] = Field(default_factory=list)


class DeploymentLookupArgs(SchemaModel):
    service_id: str
    incident_id: str | None = None
    limit: int = 10


class DeploymentRecord(SchemaModel):
    deployment_id: str
    commit_ref: str
    actor: str
    deployed_at: datetime


class DeploymentLookupResult(SchemaModel):
    deployments: list[DeploymentRecord] = Field(default_factory=list)


class ServiceRegistryLookupArgs(SchemaModel):
    service_id: str | None = None
    search_query: str | None = None


class ServiceRegistryRecord(SchemaModel):
    service_id: str
    name: str
    owner_team: str
    dependency_names: list[str] = Field(default_factory=list)
    runbook_refs: list[str] = Field(default_factory=list)


class ServiceRegistryLookupResult(SchemaModel):
    services: list[ServiceRegistryRecord] = Field(default_factory=list)


class RunbookSearchArgs(SchemaModel):
    service_id: str
    query: str
    limit: int = 5


class RunbookRecord(SchemaModel):
    runbook_id: str
    title: str
    excerpt: str
    score: float


class RunbookSearchResult(SchemaModel):
    runbooks: list[RunbookRecord] = Field(default_factory=list)


class CommsChannelPreviewArgs(SchemaModel):
    channel_type: str
    draft_body: str


class CommsChannelPreviewResult(SchemaModel):
    preview_body: str
    max_length: int
    policy_warnings: list[str] = Field(default_factory=list)


class ApprovalTaskReadStateArgs(SchemaModel):
    approval_task_ids: list[str] = Field(min_length=1)


class ApprovalTaskState(SchemaModel):
    approval_task_id: str
    status: str
    resolved_at: datetime | None = None


class ApprovalTaskReadStateResult(SchemaModel):
    approvals: list[ApprovalTaskState] = Field(default_factory=list)


class TriageOutput(SchemaModel):
    dedupe_group_key: str
    severity: str
    severity_confidence: float
    title: str
    service_id: str | None = None
    blast_radius_summary: str


class VerificationStep(SchemaModel):
    step_order: int
    instruction_text: str


class HypothesisOutput(SchemaModel):
    title: str
    confidence: float
    rank: int
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    verification_steps: list[VerificationStep] = Field(min_length=1)


class InvestigatorOutput(SchemaModel):
    hypotheses: list[HypothesisOutput] = Field(min_length=1)


class RecommendationOutput(SchemaModel):
    recommendation_type: str
    risk_level: str
    requires_approval: bool
    title: str
    instructions_markdown: str
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)


class RunbookAdvisorOutput(SchemaModel):
    recommendations: list[RecommendationOutput] = Field(min_length=1)


class CommsDraftOutput(SchemaModel):
    channel_type: str
    fact_set_version: int
    body_markdown: str
    fact_refs: list[dict[str, Any]] = Field(min_length=1)


class CommsOutput(SchemaModel):
    drafts: list[CommsDraftOutput] = Field(min_length=1)


class FollowUpAction(SchemaModel):
    title: str
    owner_hint: str


class PostmortemOutput(SchemaModel):
    postmortem_markdown: str
    follow_up_actions: list[FollowUpAction] = Field(default_factory=list)
    replay_capture_hints: list[str] = Field(default_factory=list)


def register_opsgraph(catalog: RuntimeCatalog) -> None:
    catalog.schemas.register("opsgraph.triage.output.v1", TriageOutput)
    catalog.schemas.register("opsgraph.investigator.output.v1", InvestigatorOutput)
    catalog.schemas.register("opsgraph.runbook_advisor.output.v1", RunbookAdvisorOutput)
    catalog.schemas.register("opsgraph.comms.output.v1", CommsOutput)
    catalog.schemas.register("opsgraph.postmortem.output.v1", PostmortemOutput)

    catalog.schemas.register("opsgraph.signal.read.args.v1", SignalReadArgs)
    catalog.schemas.register("opsgraph.signal.read.result.v1", SignalReadResult)
    catalog.schemas.register("opsgraph.incident.read_timeline.args.v1", IncidentReadTimelineArgs)
    catalog.schemas.register("opsgraph.incident.read_timeline.result.v1", IncidentReadTimelineResult)
    catalog.schemas.register("opsgraph.context_bundle.read.args.v1", ContextBundleReadArgs)
    catalog.schemas.register("opsgraph.context_bundle.read.result.v1", ContextBundleReadResult)
    catalog.schemas.register("opsgraph.deployment.lookup.args.v1", DeploymentLookupArgs)
    catalog.schemas.register("opsgraph.deployment.lookup.result.v1", DeploymentLookupResult)
    catalog.schemas.register("opsgraph.service_registry.lookup.args.v1", ServiceRegistryLookupArgs)
    catalog.schemas.register("opsgraph.service_registry.lookup.result.v1", ServiceRegistryLookupResult)
    catalog.schemas.register("opsgraph.runbook.search.args.v1", RunbookSearchArgs)
    catalog.schemas.register("opsgraph.runbook.search.result.v1", RunbookSearchResult)
    catalog.schemas.register("opsgraph.comms.channel_preview.args.v1", CommsChannelPreviewArgs)
    catalog.schemas.register("opsgraph.comms.channel_preview.result.v1", CommsChannelPreviewResult)
    catalog.schemas.register("opsgraph.approval_task.read_state.args.v1", ApprovalTaskReadStateArgs)
    catalog.schemas.register("opsgraph.approval_task.read_state.result.v1", ApprovalTaskReadStateResult)

    for tool in (
        ToolDefinition(
            tool_name="signal.read",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="opsgraph.signal.read.args.v1",
            output_schema_ref="opsgraph.signal.read.result.v1",
            adapter_type="opsgraph_database",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="incident.read_timeline",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="opsgraph.incident.read_timeline.args.v1",
            output_schema_ref="opsgraph.incident.read_timeline.result.v1",
            adapter_type="opsgraph_database",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="context_bundle.read",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="opsgraph.context_bundle.read.args.v1",
            output_schema_ref="opsgraph.context_bundle.read.result.v1",
            adapter_type="context_bundle_reader",
            idempotency_scope="subject",
            default_timeout_ms=7_500,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="deployment.lookup",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="opsgraph.deployment.lookup.args.v1",
            output_schema_ref="opsgraph.deployment.lookup.result.v1",
            adapter_type="github",
            idempotency_scope="subject",
            default_timeout_ms=7_500,
            auth_context_source="workspace_connection",
        ),
        ToolDefinition(
            tool_name="service_registry.lookup",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="opsgraph.service_registry.lookup.args.v1",
            output_schema_ref="opsgraph.service_registry.lookup.result.v1",
            adapter_type="service_registry",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="runbook.search",
            tool_version=TOOL_VERSION,
            category="search",
            access_mode="read_only",
            input_schema_ref="opsgraph.runbook.search.args.v1",
            output_schema_ref="opsgraph.runbook.search.result.v1",
            adapter_type="vector_store",
            idempotency_scope="none",
            default_timeout_ms=7_500,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="comms.channel_preview",
            tool_version=TOOL_VERSION,
            category="comms",
            access_mode="read_only",
            input_schema_ref="opsgraph.comms.channel_preview.args.v1",
            output_schema_ref="opsgraph.comms.channel_preview.result.v1",
            adapter_type="channel_policy",
            idempotency_scope="request",
            default_timeout_ms=5_000,
            auth_context_source="workspace_connection",
        ),
        ToolDefinition(
            tool_name="approval_task.read_state",
            tool_version=TOOL_VERSION,
            category="approval",
            access_mode="read_only",
            input_schema_ref="opsgraph.approval_task.read_state.args.v1",
            output_schema_ref="opsgraph.approval_task.read_state.result.v1",
            adapter_type="approval_store",
            idempotency_scope="request",
            default_timeout_ms=5_000,
            auth_context_source="platform_database",
        ),
    ):
        catalog.tools.register(tool.tool_name, tool.tool_version, tool)

    policies = (
        ToolPolicy(
            tool_policy_id="opsgraph.triage.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="triage_agent",
            allowed_tools=[_tool_entry("signal.read"), _tool_entry("service_registry.lookup")],
            max_tool_calls_per_turn=4,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
        ToolPolicy(
            tool_policy_id="opsgraph.investigator.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="investigator_agent",
            allowed_tools=[
                _tool_entry("signal.read"),
                _tool_entry("incident.read_timeline"),
                _tool_entry("context_bundle.read"),
                _tool_entry("deployment.lookup"),
                _tool_entry("service_registry.lookup"),
            ],
            max_tool_calls_per_turn=8,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
        ToolPolicy(
            tool_policy_id="opsgraph.runbook_advisor.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="runbook_advisor",
            allowed_tools=[
                _tool_entry("context_bundle.read"),
                _tool_entry("deployment.lookup"),
                _tool_entry("service_registry.lookup"),
                _tool_entry("runbook.search"),
                _tool_entry("approval_task.read_state"),
            ],
            max_tool_calls_per_turn=8,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
        ToolPolicy(
            tool_policy_id="opsgraph.comms.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="comms_agent",
            allowed_tools=[
                _tool_entry("incident.read_timeline"),
                _tool_entry("context_bundle.read"),
                _tool_entry("comms.channel_preview"),
                _tool_entry("approval_task.read_state"),
            ],
            max_tool_calls_per_turn=6,
            allow_parallel_calls=False,
            degraded_mode_behavior="fail_closed",
        ),
        ToolPolicy(
            tool_policy_id="opsgraph.postmortem.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="postmortem_reviewer",
            allowed_tools=[
                _tool_entry("incident.read_timeline"),
                _tool_entry("context_bundle.read"),
                _tool_entry("deployment.lookup"),
                _tool_entry("service_registry.lookup"),
            ],
            max_tool_calls_per_turn=6,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
    )
    for policy in policies:
        catalog.tool_policies.register(policy.tool_policy_id, policy.tool_policy_version, policy)

    prompt_bundles = (
        PromptBundle(
            bundle_id="opsgraph.triage",
            bundle_version=BUNDLE_VERSION,
            workflow_type="opsgraph_incident",
            agent_name="triage_agent",
            prompt_parts=[
                _part("system_identity", "Incident triage classifier."),
                _part("developer_constraints", "Recommend severity conservatively when confidence is low."),
                _part("runtime_context", "Signal ids and environment metadata.", ["signal_ids", "environment_name"]),
                _part("domain_context", "Normalized signals and service candidates.", ["signal_summaries", "current_incident_candidates"]),
                _part("tool_manifest", "Read-only triage tools."),
                _part("output_contract", "Triage output schema."),
            ],
            variable_contract=[
                _var("signal_ids", "workflow_state"),
                _var("signal_summaries", "database", transform=["truncate"]),
                _var("environment_name", "computed"),
                _var("current_incident_candidates", "database", required=False),
            ],
            response_schema_ref="opsgraph.triage.output.v1",
            model_profile_id="classification.standard",
            tool_policy_id="opsgraph.triage.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="none",
            citation_policy_version="v1",
            context_budget_profile="fast.triage.v1",
        ),
        PromptBundle(
            bundle_id="opsgraph.investigator",
            bundle_version=BUNDLE_VERSION,
            workflow_type="opsgraph_incident",
            agent_name="investigator_agent",
            prompt_parts=[
                _part("system_identity", "Incident investigator separating facts from hypotheses."),
                _part("developer_constraints", "Never label a hypothesis as a confirmed fact."),
                _part("runtime_context", "Incident status and missing source warnings.", ["incident_id", "current_fact_set_version", "context_missing_sources"]),
                _part("domain_context", "Context bundle, confirmed facts, deploys, and service dependencies.", ["context_bundle_id", "confirmed_fact_refs"]),
                _part("memory_context", "Recent incident patterns for the same service.", ["memory_context"]),
                _part("tool_manifest", "Read-only incident investigation tools."),
                _part("output_contract", "Investigator output schema."),
            ],
            variable_contract=[
                _var("incident_id", "workflow_state"),
                _var("context_bundle_id", "workflow_state"),
                _var("current_fact_set_version", "workflow_state"),
                _var("confirmed_fact_refs", "database"),
                _var("context_missing_sources", "workflow_state"),
                _var("memory_context", "memory", required=False),
            ],
            response_schema_ref="opsgraph.investigator.output.v1",
            model_profile_id="reasoning.standard",
            tool_policy_id="opsgraph.investigator.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="facts.required",
            citation_policy_version="v1",
            context_budget_profile="long_context.investigation.v1",
        ),
        PromptBundle(
            bundle_id="opsgraph.runbook_advisor",
            bundle_version=BUNDLE_VERSION,
            workflow_type="opsgraph_incident",
            agent_name="runbook_advisor",
            prompt_parts=[
                _part("system_identity", "Runbook-based incident advisor."),
                _part("developer_constraints", "Do not execute actions and classify risk conservatively."),
                _part("runtime_context", "Incident, severity, and mitigation policy.", ["incident_id", "current_fact_set_version", "service_id"]),
                _part("domain_context", "Confirmed facts, top hypotheses, runbooks, and deploy context.", ["confirmed_fact_refs", "top_hypothesis_refs"]),
                _part("memory_context", "Prior successful mitigations for the same service.", ["memory_context"]),
                _part("tool_manifest", "Read-only recommendation tools."),
                _part("output_contract", "Runbook advisor output schema."),
            ],
            variable_contract=[
                _var("incident_id", "workflow_state"),
                _var("current_fact_set_version", "workflow_state"),
                _var("confirmed_fact_refs", "database"),
                _var("top_hypothesis_refs", "database"),
                _var("service_id", "workflow_state"),
                _var("memory_context", "memory", required=False),
            ],
            response_schema_ref="opsgraph.runbook_advisor.output.v1",
            model_profile_id="reasoning.standard",
            tool_policy_id="opsgraph.runbook_advisor.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="facts.required",
            citation_policy_version="v1",
            context_budget_profile="long_context.recommendation.v1",
        ),
        PromptBundle(
            bundle_id="opsgraph.comms",
            bundle_version=BUNDLE_VERSION,
            workflow_type="opsgraph_incident",
            agent_name="comms_agent",
            prompt_parts=[
                _part("system_identity", "Grounded incident communications writer."),
                _part("developer_constraints", "Use confirmed facts only and match channel policy."),
                _part("runtime_context", "Incident, fact set, target channels, and publish policy.", ["incident_id", "current_fact_set_version", "target_channels", "channel_policy"]),
                _part("domain_context", "Confirmed facts and safe timeline excerpts.", ["confirmed_fact_refs"]),
                _part("tool_manifest", "Read-only draft validation tools."),
                _part("output_contract", "Comms output schema."),
            ],
            variable_contract=[
                _var("incident_id", "workflow_state"),
                _var("current_fact_set_version", "workflow_state"),
                _var("confirmed_fact_refs", "database"),
                _var("target_channels", "trigger_payload"),
                _var("channel_policy", "computed"),
            ],
            response_schema_ref="opsgraph.comms.output.v1",
            model_profile_id="generation.grounded",
            tool_policy_id="opsgraph.comms.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="facts.required",
            citation_policy_version="v1",
            context_budget_profile="fact_bound.comms.v1",
        ),
        PromptBundle(
            bundle_id="opsgraph.postmortem_reviewer",
            bundle_version=BUNDLE_VERSION,
            workflow_type="opsgraph_incident",
            agent_name="postmortem_reviewer",
            prompt_parts=[
                _part("system_identity", "Incident postmortem writer using confirmed timeline and facts."),
                _part("developer_constraints", "Do not invent missing causality or root cause."),
                _part("runtime_context", "Incident, resolution state, and final fact set.", ["incident_id", "current_fact_set_version", "resolution_summary"]),
                _part("domain_context", "Confirmed facts, timeline events, and deploy refs.", ["confirmed_fact_refs", "timeline_refs"]),
                _part("memory_context", "Organization postmortem style preferences.", ["memory_context"]),
                _part("tool_manifest", "Read-only retrospective tools."),
                _part("output_contract", "Postmortem output schema."),
            ],
            variable_contract=[
                _var("incident_id", "workflow_state"),
                _var("current_fact_set_version", "workflow_state"),
                _var("confirmed_fact_refs", "database"),
                _var("timeline_refs", "database"),
                _var("resolution_summary", "database"),
                _var("memory_context", "memory", required=False),
            ],
            response_schema_ref="opsgraph.postmortem.output.v1",
            model_profile_id="generation.grounded",
            tool_policy_id="opsgraph.postmortem.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="facts.required",
            citation_policy_version="v1",
            context_budget_profile="retrospective.generation.v1",
        ),
    )
    for bundle in prompt_bundles:
        catalog.prompt_bundles.register(bundle.bundle_id, bundle.bundle_version, bundle)
