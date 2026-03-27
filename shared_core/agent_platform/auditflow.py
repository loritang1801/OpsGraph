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


class ReviewBlocker(SchemaModel):
    kind: Literal["mapping", "gap"]
    id: str
    severity: Literal["low", "medium", "high", "critical"]
    reason_code: str


class AuditCycleWorkflowState(SharedWorkflowStateEnvelope):
    workflow_type: Literal["auditflow_cycle"] = "auditflow_cycle"
    subject_type: Literal["audit_cycle"] = "audit_cycle"
    current_state: str = "workspace_setup"
    audit_workspace_id: str
    audit_cycle_id: str
    cycle_status: str
    working_snapshot_version: int
    requested_source_ids: list[str] = Field(default_factory=list)
    parsed_evidence_ids: list[str] = Field(default_factory=list)
    failed_source_ids: list[str] = Field(default_factory=list)
    proposed_mapping_ids: list[str] = Field(default_factory=list)
    flagged_mapping_ids: list[str] = Field(default_factory=list)
    open_gap_ids: list[str] = Field(default_factory=list)
    review_blocker_count: int = 0
    narrative_ids: list[str] = Field(default_factory=list)
    package_id: str | None = None
    partial_ingestion: bool = False
    export_requested: bool = False


class ArtifactReadArgs(SchemaModel):
    artifact_id: str


class ArtifactReadResult(SchemaModel):
    artifact_id: str
    artifact_type: str
    parser_status: str
    text_ref_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactPreviewChunkArgs(SchemaModel):
    artifact_id: str
    chunk_id: str


class ArtifactPreviewChunkResult(SchemaModel):
    artifact_id: str
    chunk_id: str
    text: str
    locator: dict[str, Any] | None = None


class EvidenceSearchArgs(SchemaModel):
    workspace_id: str
    audit_cycle_id: str
    query: str
    limit: int = 5


class EvidenceSearchItem(SchemaModel):
    evidence_chunk_id: str
    evidence_item_id: str
    score: float
    summary: str


class EvidenceSearchResult(SchemaModel):
    items: list[EvidenceSearchItem] = Field(default_factory=list)


class ControlCatalogLookupArgs(SchemaModel):
    control_ids: list[str] = Field(default_factory=list)
    framework_name: str | None = None
    search_query: str | None = None


class ControlCatalogItem(SchemaModel):
    control_id: str
    title: str
    objective_text: str


class ControlCatalogLookupResult(SchemaModel):
    controls: list[ControlCatalogItem] = Field(default_factory=list)


class MappingReadCandidatesArgs(SchemaModel):
    audit_cycle_id: str
    evidence_item_id: str | None = None
    control_id: str | None = None


class MappingCandidateRecord(SchemaModel):
    mapping_id: str
    control_id: str
    status: str
    ranking_score: float


class MappingReadCandidatesResult(SchemaModel):
    candidates: list[MappingCandidateRecord] = Field(default_factory=list)


class ReviewDecisionReadHistoryArgs(SchemaModel):
    audit_cycle_id: str
    control_id: str | None = None
    mapping_id: str | None = None


class ReviewDecisionSummary(SchemaModel):
    review_decision_id: str
    decision: str
    decided_at: datetime


class ReviewDecisionReadHistoryResult(SchemaModel):
    decisions: list[ReviewDecisionSummary] = Field(default_factory=list)


class NarrativeSnapshotReadArgs(SchemaModel):
    audit_cycle_id: str
    working_snapshot_version: int


class NarrativeSnapshotReadResult(SchemaModel):
    accepted_mapping_ids: list[str] = Field(default_factory=list)
    open_gap_ids: list[str] = Field(default_factory=list)
    prior_narrative_ids: list[str] = Field(default_factory=list)


class ExportSnapshotValidateArgs(SchemaModel):
    audit_cycle_id: str
    working_snapshot_version: int


class ExportSnapshotValidateResult(SchemaModel):
    eligible: bool
    blocker_codes: list[str] = Field(default_factory=list)
    current_snapshot_version: int


class CollectorOutput(SchemaModel):
    normalized_title: str
    evidence_type: str
    summary: str
    captured_at: datetime | None = None
    fresh_until: datetime | None = None
    citation_refs: list[dict[str, Any]] = Field(min_length=1)


class MappingCandidateOutput(SchemaModel):
    control_id: str
    confidence: float
    ranking_score: float
    rationale: str
    citation_refs: list[dict[str, Any]] = Field(min_length=1)


class MapperOutput(SchemaModel):
    mapping_candidates: list[MappingCandidateOutput] = Field(min_length=1)


class MappingFlagOutput(SchemaModel):
    mapping_id: str
    issue_type: str
    severity: str
    recommended_action: str


class GapOutput(SchemaModel):
    control_state_id: str
    gap_type: str
    severity: str
    title: str


class SkepticOutput(SchemaModel):
    mapping_flags: list[MappingFlagOutput] = Field(default_factory=list)
    gaps: list[GapOutput] = Field(default_factory=list)


class NarrativeOutput(SchemaModel):
    control_state_id: str
    narrative_type: str
    content_markdown: str
    citation_refs: list[dict[str, Any]] = Field(min_length=1)


class WriterOutput(SchemaModel):
    narratives: list[NarrativeOutput] = Field(min_length=1)


class ReviewCoordinatorOutput(SchemaModel):
    review_blocker_count: int
    ready_for_export: bool
    blocking_ids: list[str] = Field(default_factory=list)
    recommended_focus: str


def register_auditflow(catalog: RuntimeCatalog) -> None:
    catalog.schemas.register("auditflow.collector.output.v1", CollectorOutput)
    catalog.schemas.register("auditflow.mapper.output.v1", MapperOutput)
    catalog.schemas.register("auditflow.skeptic.output.v1", SkepticOutput)
    catalog.schemas.register("auditflow.writer.output.v1", WriterOutput)
    catalog.schemas.register("auditflow.review_coordinator.output.v1", ReviewCoordinatorOutput)

    catalog.schemas.register("auditflow.artifact.read.args.v1", ArtifactReadArgs)
    catalog.schemas.register("auditflow.artifact.read.result.v1", ArtifactReadResult)
    catalog.schemas.register("auditflow.artifact.preview_chunk.args.v1", ArtifactPreviewChunkArgs)
    catalog.schemas.register("auditflow.artifact.preview_chunk.result.v1", ArtifactPreviewChunkResult)
    catalog.schemas.register("auditflow.evidence.search.args.v1", EvidenceSearchArgs)
    catalog.schemas.register("auditflow.evidence.search.result.v1", EvidenceSearchResult)
    catalog.schemas.register("auditflow.control_catalog.lookup.args.v1", ControlCatalogLookupArgs)
    catalog.schemas.register("auditflow.control_catalog.lookup.result.v1", ControlCatalogLookupResult)
    catalog.schemas.register("auditflow.mapping.read_candidates.args.v1", MappingReadCandidatesArgs)
    catalog.schemas.register("auditflow.mapping.read_candidates.result.v1", MappingReadCandidatesResult)
    catalog.schemas.register("auditflow.review_decision.read_history.args.v1", ReviewDecisionReadHistoryArgs)
    catalog.schemas.register("auditflow.review_decision.read_history.result.v1", ReviewDecisionReadHistoryResult)
    catalog.schemas.register("auditflow.narrative.snapshot_read.args.v1", NarrativeSnapshotReadArgs)
    catalog.schemas.register("auditflow.narrative.snapshot_read.result.v1", NarrativeSnapshotReadResult)
    catalog.schemas.register("auditflow.export.snapshot_validate.args.v1", ExportSnapshotValidateArgs)
    catalog.schemas.register("auditflow.export.snapshot_validate.result.v1", ExportSnapshotValidateResult)

    for tool in (
        ToolDefinition(
            tool_name="artifact.read",
            tool_version=TOOL_VERSION,
            category="artifact",
            access_mode="read_only",
            input_schema_ref="auditflow.artifact.read.args.v1",
            output_schema_ref="auditflow.artifact.read.result.v1",
            adapter_type="artifact_store",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_connection",
        ),
        ToolDefinition(
            tool_name="artifact.preview_chunk",
            tool_version=TOOL_VERSION,
            category="artifact",
            access_mode="read_only",
            input_schema_ref="auditflow.artifact.preview_chunk.args.v1",
            output_schema_ref="auditflow.artifact.preview_chunk.result.v1",
            adapter_type="chunk_store",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_connection",
        ),
        ToolDefinition(
            tool_name="evidence.search",
            tool_version=TOOL_VERSION,
            category="search",
            access_mode="read_only",
            input_schema_ref="auditflow.evidence.search.args.v1",
            output_schema_ref="auditflow.evidence.search.result.v1",
            adapter_type="vector_store",
            idempotency_scope="none",
            default_timeout_ms=7_500,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="control_catalog.lookup",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="auditflow.control_catalog.lookup.args.v1",
            output_schema_ref="auditflow.control_catalog.lookup.result.v1",
            adapter_type="control_catalog",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="mapping.read_candidates",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="auditflow.mapping.read_candidates.args.v1",
            output_schema_ref="auditflow.mapping.read_candidates.result.v1",
            adapter_type="auditflow_database",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="review_decision.read_history",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="auditflow.review_decision.read_history.args.v1",
            output_schema_ref="auditflow.review_decision.read_history.result.v1",
            adapter_type="auditflow_database",
            idempotency_scope="none",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="narrative.snapshot_read",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="auditflow.narrative.snapshot_read.args.v1",
            output_schema_ref="auditflow.narrative.snapshot_read.result.v1",
            adapter_type="snapshot_reader",
            idempotency_scope="subject",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
        ToolDefinition(
            tool_name="export.snapshot_validate",
            tool_version=TOOL_VERSION,
            category="lookup",
            access_mode="read_only",
            input_schema_ref="auditflow.export.snapshot_validate.args.v1",
            output_schema_ref="auditflow.export.snapshot_validate.result.v1",
            adapter_type="snapshot_validator",
            idempotency_scope="subject",
            default_timeout_ms=5_000,
            auth_context_source="workspace_database",
        ),
    ):
        catalog.tools.register(tool.tool_name, tool.tool_version, tool)

    policies = (
        ToolPolicy(
            tool_policy_id="auditflow.collector.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="collector_agent",
            allowed_tools=[_tool_entry("artifact.read"), _tool_entry("artifact.preview_chunk")],
            max_tool_calls_per_turn=3,
            allow_parallel_calls=False,
            degraded_mode_behavior="fail_closed",
        ),
        ToolPolicy(
            tool_policy_id="auditflow.mapper.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="mapper_agent",
            allowed_tools=[
                _tool_entry("evidence.search"),
                _tool_entry("control_catalog.lookup"),
                _tool_entry("mapping.read_candidates"),
            ],
            max_tool_calls_per_turn=6,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
        ToolPolicy(
            tool_policy_id="auditflow.skeptic.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="skeptic_agent",
            allowed_tools=[
                _tool_entry("evidence.search"),
                _tool_entry("control_catalog.lookup"),
                _tool_entry("mapping.read_candidates"),
                _tool_entry("review_decision.read_history"),
            ],
            max_tool_calls_per_turn=8,
            allow_parallel_calls=True,
            degraded_mode_behavior="continue_partial",
        ),
        ToolPolicy(
            tool_policy_id="auditflow.writer.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="writer_agent",
            allowed_tools=[
                _tool_entry("narrative.snapshot_read"),
                _tool_entry("control_catalog.lookup"),
                _tool_entry("export.snapshot_validate"),
            ],
            max_tool_calls_per_turn=5,
            allow_parallel_calls=False,
            degraded_mode_behavior="fail_closed",
        ),
        ToolPolicy(
            tool_policy_id="auditflow.review_coordinator.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            agent_name="review_coordinator",
            allowed_tools=[
                _tool_entry("mapping.read_candidates"),
                _tool_entry("review_decision.read_history"),
                _tool_entry("export.snapshot_validate"),
            ],
            max_tool_calls_per_turn=5,
            allow_parallel_calls=False,
            degraded_mode_behavior="continue_partial",
        ),
    )
    for policy in policies:
        catalog.tool_policies.register(policy.tool_policy_id, policy.tool_policy_version, policy)

    prompt_bundles = (
        PromptBundle(
            bundle_id="auditflow.collector",
            bundle_version=BUNDLE_VERSION,
            workflow_type="auditflow_cycle",
            agent_name="collector_agent",
            prompt_parts=[
                _part("system_identity", "Evidence normalization specialist for SOC 2 support material."),
                _part("developer_constraints", "Return only schema fields and do not infer uncited facts."),
                _part(
                    "runtime_context",
                    "Cycle, source, and workspace policy context.",
                    ["audit_cycle_id", "source_id", "source_type", "allowed_evidence_types"],
                ),
                _part("domain_context", "Artifact metadata and extracted text preview.", ["artifact_id", "extracted_text_or_summary"]),
                _part("output_contract", "Collector output schema."),
            ],
            variable_contract=[
                _var("audit_cycle_id", "workflow_state"),
                _var("source_id", "workflow_state"),
                _var("source_type", "workflow_state"),
                _var("artifact_id", "database"),
                _var("extracted_text_or_summary", "database", transform=["truncate"]),
                _var("allowed_evidence_types", "computed"),
            ],
            response_schema_ref="auditflow.collector.output.v1",
            model_profile_id="extraction.standard",
            tool_policy_id="auditflow.collector.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="evidence.required",
            citation_policy_version="v1",
            context_budget_profile="compact.extraction.v1",
        ),
        PromptBundle(
            bundle_id="auditflow.mapper",
            bundle_version=BUNDLE_VERSION,
            workflow_type="auditflow_cycle",
            agent_name="mapper_agent",
            prompt_parts=[
                _part("system_identity", "Control-evidence mapping specialist."),
                _part("developer_constraints", "Map only to in-scope controls and return grounded rationales."),
                _part("runtime_context", "Cycle, framework, and evidence metadata.", ["audit_cycle_id", "evidence_item_id", "framework_name"]),
                _part("domain_context", "Evidence chunks and candidate controls.", ["evidence_chunk_refs", "in_scope_controls"]),
                _part("memory_context", "Accepted reviewer preferences for the same framework.", ["accepted_pattern_memories"]),
                _part("tool_manifest", "Read-only mapping tools."),
                _part("output_contract", "Mapper output schema."),
            ],
            variable_contract=[
                _var("audit_cycle_id", "workflow_state"),
                _var("evidence_item_id", "workflow_state"),
                _var("evidence_chunk_refs", "retrieval", transform=["top_k"]),
                _var("in_scope_controls", "database"),
                _var("framework_name", "database"),
                _var("accepted_pattern_memories", "memory", transform=["top_k"]),
            ],
            response_schema_ref="auditflow.mapper.output.v1",
            model_profile_id="reasoning.standard",
            tool_policy_id="auditflow.mapper.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="evidence.required",
            citation_policy_version="v1",
            context_budget_profile="long_context.reasoning.v1",
        ),
        PromptBundle(
            bundle_id="auditflow.skeptic",
            bundle_version=BUNDLE_VERSION,
            workflow_type="auditflow_cycle",
            agent_name="skeptic_agent",
            prompt_parts=[
                _part("system_identity", "Evidence quality and contradiction reviewer."),
                _part("developer_constraints", "Prefer under-claiming to over-claiming control coverage."),
                _part("runtime_context", "Cycle, freshness policy, and mapping ids.", ["proposed_mapping_ids", "freshness_policy"]),
                _part("domain_context", "Mappings, evidence chunks, and existing gaps.", ["mapping_payloads", "control_text"]),
                _part("memory_context", "Prior rejection patterns for the same control.", ["challenge_pattern_memories"]),
                _part("tool_manifest", "Read-only challenge tools."),
                _part("output_contract", "Skeptic output schema."),
            ],
            variable_contract=[
                _var("proposed_mapping_ids", "workflow_state"),
                _var("mapping_payloads", "database", transform=["truncate"]),
                _var("freshness_policy", "computed"),
                _var("control_text", "database"),
                _var("challenge_pattern_memories", "memory", transform=["top_k"]),
            ],
            response_schema_ref="auditflow.skeptic.output.v1",
            model_profile_id="reasoning.standard",
            tool_policy_id="auditflow.skeptic.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="evidence.required",
            citation_policy_version="v1",
            context_budget_profile="long_context.challenge.v1",
        ),
        PromptBundle(
            bundle_id="auditflow.writer",
            bundle_version=BUNDLE_VERSION,
            workflow_type="auditflow_cycle",
            agent_name="writer_agent",
            prompt_parts=[
                _part("system_identity", "Audit narrative writer operating on frozen snapshot data."),
                _part("developer_constraints", "Do not introduce facts outside the fixed snapshot."),
                _part("runtime_context", "Cycle, snapshot, and export scope.", ["audit_cycle_id", "working_snapshot_version", "export_scope"]),
                _part("domain_context", "Accepted mappings, gaps, and prior narratives.", ["accepted_mapping_refs", "open_gap_refs"]),
                _part("tool_manifest", "Snapshot-bound read-only writing tools."),
                _part("output_contract", "Writer output schema."),
            ],
            variable_contract=[
                _var("audit_cycle_id", "workflow_state"),
                _var("working_snapshot_version", "workflow_state"),
                _var("accepted_mapping_refs", "database"),
                _var("open_gap_refs", "database"),
                _var("export_scope", "trigger_payload"),
            ],
            response_schema_ref="auditflow.writer.output.v1",
            model_profile_id="generation.grounded",
            tool_policy_id="auditflow.writer.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="evidence.required",
            citation_policy_version="v1",
            context_budget_profile="snapshot.generation.v1",
        ),
        PromptBundle(
            bundle_id="auditflow.review_coordinator",
            bundle_version=BUNDLE_VERSION,
            workflow_type="auditflow_cycle",
            agent_name="review_coordinator",
            prompt_parts=[
                _part("system_identity", "Review queue summarizer and readiness coordinator."),
                _part("developer_constraints", "Summarize current state without issuing final review decisions."),
                _part("runtime_context", "Blocker counts and snapshot state.", ["review_blocker_count", "working_snapshot_version"]),
                _part("domain_context", "Pending mappings, gaps, and recent review outcomes.", ["pending_mapping_refs", "open_gap_refs"]),
                _part("tool_manifest", "Read-only review coordination tools."),
                _part("output_contract", "Review coordinator output schema."),
            ],
            variable_contract=[
                _var("review_blocker_count", "workflow_state"),
                _var("pending_mapping_refs", "database"),
                _var("open_gap_refs", "database"),
                _var("working_snapshot_version", "workflow_state"),
            ],
            response_schema_ref="auditflow.review_coordinator.output.v1",
            model_profile_id="summarization.compact",
            tool_policy_id="auditflow.review_coordinator.policy",
            tool_policy_version=TOOL_POLICY_VERSION,
            citation_policy_id="none",
            citation_policy_version="v1",
            context_budget_profile="compact.review.v1",
        ),
    )
    for bundle in prompt_bundles:
        catalog.prompt_bundles.register(bundle.bundle_id, bundle.bundle_version, bundle)
