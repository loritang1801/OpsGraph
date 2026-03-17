from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpsGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactSummary(OpsGraphModel):
    fact_id: str
    fact_type: str
    status: str
    statement: str
    fact_set_version: int
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class IncidentSummary(OpsGraphModel):
    incident_id: str
    incident_key: str
    title: str
    severity: str
    incident_status: str
    service_name: str
    opened_at: datetime
    current_fact_set_version: int
    latest_workflow_run_id: str | None = None
    updated_at: datetime | None = None


class HypothesisSummary(OpsGraphModel):
    hypothesis_id: str
    status: str
    rank: int
    confidence: float | None = None
    title: str
    rationale: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime


class RecommendationSummary(OpsGraphModel):
    recommendation_id: str
    title: str
    risk_level: str
    approval_required: bool
    status: str
    hypothesis_id: str | None = None
    approval_task_id: str | None = None


class ApprovalTaskSummary(OpsGraphModel):
    approval_task_id: str
    incident_id: str
    recommendation_id: str | None = None
    status: str
    comment: str | None = None
    created_at: datetime
    updated_at: datetime


class CommsDraftSummary(OpsGraphModel):
    draft_id: str
    channel: str
    title: str
    status: str
    fact_set_version: int
    published_message_ref: str | None = None


class TimelineEventSummary(OpsGraphModel):
    event_id: str
    kind: str
    summary: str
    created_at: datetime


class PostmortemSummary(OpsGraphModel):
    postmortem_id: str
    incident_id: str
    status: str
    fact_set_version: int
    artifact_id: str | None = None
    replay_case_id: str | None = None
    updated_at: datetime


class ReplayRunSummary(OpsGraphModel):
    replay_run_id: str
    incident_id: str
    status: str
    model_bundle_version: str
    replay_case_id: str | None = None
    workflow_run_id: str | None = None
    current_state: str | None = None
    error_message: str | None = None
    created_at: datetime


class ReplayCaseSummary(OpsGraphModel):
    replay_case_id: str
    incident_id: str
    workflow_type: str
    subject_type: str
    subject_id: str
    case_name: str
    source_workflow_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ReplayCaseDetail(ReplayCaseSummary):
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] | None = None


class ReplayNodeSummary(OpsGraphModel):
    checkpoint_seq: int
    bundle_id: str
    bundle_version: str
    output_summary: str
    recorded_at: datetime | None = None


class ReplayNodeDiffSummary(OpsGraphModel):
    checkpoint_seq: int
    matched: bool
    expected_bundle_id: str
    actual_bundle_id: str | None = None
    expected_bundle_version: str
    actual_bundle_version: str | None = None
    expected_output_summary: str
    actual_output_summary: str | None = None
    baseline_elapsed_ms: int | None = None
    replay_elapsed_ms: int | None = None
    latency_delta_ms: int | None = None
    mismatch_reasons: list[str] = Field(default_factory=list)


class ReplayBaselineCaptureCommand(OpsGraphModel):
    incident_id: str
    model_bundle_version: str
    workflow_run_id: str | None = None


class ReplayBaselineSummary(OpsGraphModel):
    baseline_id: str
    incident_id: str
    workflow_run_id: str
    model_bundle_version: str
    workflow_type: str
    final_state: str
    checkpoint_seq: int
    node_summaries: list[ReplayNodeSummary] = Field(default_factory=list)
    created_at: datetime


class ReplayEvaluationCommand(OpsGraphModel):
    baseline_id: str


class ReplayEvaluationSummary(OpsGraphModel):
    report_id: str
    baseline_id: str
    replay_run_id: str
    incident_id: str
    status: Literal["matched", "mismatched"]
    score: float
    mismatch_count: int
    matched_node_count: int = 0
    mismatched_node_count: int = 0
    bundle_mismatch_count: int = 0
    summary_mismatch_count: int = 0
    latency_regression_count: int = 0
    max_latency_delta_ms: int | None = None
    mismatches: list[str] = Field(default_factory=list)
    baseline_final_state: str | None = None
    replay_final_state: str | None = None
    baseline_checkpoint_seq: int | None = None
    replay_checkpoint_seq: int | None = None
    node_diffs: list[ReplayNodeDiffSummary] = Field(default_factory=list)
    report_artifact_path: str | None = None
    markdown_report_path: str | None = None
    created_at: datetime


class IncidentWorkspaceResponse(OpsGraphModel):
    incident: IncidentSummary
    confirmed_facts: list[FactSummary] = Field(default_factory=list)
    hypotheses: list[HypothesisSummary] = Field(default_factory=list)
    recommendations: list[RecommendationSummary] = Field(default_factory=list)
    comms_drafts: list[CommsDraftSummary] = Field(default_factory=list)
    timeline: list[TimelineEventSummary] = Field(default_factory=list)


class AlertIngestCommand(OpsGraphModel):
    ops_workspace_id: str = "ops-ws-1"
    correlation_key: str
    summary: str
    source: Literal["prometheus", "grafana"] = "prometheus"
    observed_at: datetime
    organization_id: str = "org-1"
    workspace_id: str = "ws-1"


class AlertIngestResponse(OpsGraphModel):
    signal_id: str
    incident_id: str
    incident_created: bool


class FactCreateCommand(OpsGraphModel):
    fact_type: str
    statement: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    expected_fact_set_version: int


class FactMutationResponse(OpsGraphModel):
    fact_id: str
    status: str
    current_fact_set_version: int


class HypothesisDecisionCommand(OpsGraphModel):
    decision: Literal["accept", "reject"]
    comment: str = ""
    expected_updated_at: datetime | None = None


class HypothesisDecisionResponse(OpsGraphModel):
    hypothesis_id: str
    status: str


class FactRetractCommand(OpsGraphModel):
    reason: str
    expected_fact_set_version: int


class SeverityOverrideCommand(OpsGraphModel):
    severity: str
    reason: str
    expected_updated_at: datetime | None = None


class CommsPublishCommand(OpsGraphModel):
    expected_fact_set_version: int
    approval_task_id: str | None = None


class CommsPublishResponse(OpsGraphModel):
    draft_id: str
    status: str
    published_message_ref: str | None = None


class RecommendationDecisionCommand(OpsGraphModel):
    decision: Literal["approve", "reject", "mark_executed"]
    comment: str = ""
    approval_task_id: str | None = None
    expected_updated_at: datetime | None = None


class RecommendationDecisionResponse(OpsGraphModel):
    recommendation_id: str
    status: str


class ResolveIncidentCommand(OpsGraphModel):
    resolution_summary: str
    root_cause_fact_ids: list[str] = Field(default_factory=list)
    expected_updated_at: datetime | None = None


class CloseIncidentCommand(OpsGraphModel):
    close_reason: str
    expected_updated_at: datetime | None = None


class ReplayRunCommand(OpsGraphModel):
    incident_id: str | None = None
    replay_case_id: str | None = None
    model_bundle_version: str

    @model_validator(mode="after")
    def validate_selector(self) -> "ReplayRunCommand":
        if (self.incident_id is None) == (self.replay_case_id is None):
            raise ValueError("Exactly one of incident_id or replay_case_id must be provided")
        return self


class ReplayStatusCommand(OpsGraphModel):
    status: Literal["queued", "running", "completed", "failed"]


class IncidentResponseCommand(OpsGraphModel):
    workflow_run_id: str
    incident_id: str
    ops_workspace_id: str = "ops-ws-1"
    signal_ids: list[str] = Field(default_factory=list)
    signal_summaries: list[dict[str, Any]] = Field(default_factory=list)
    current_incident_candidates: list[dict[str, Any]] = Field(default_factory=list)
    context_bundle_id: str = "context-1"
    current_fact_set_version: int = 1
    confirmed_fact_refs: list[dict[str, Any]] = Field(default_factory=list)
    top_hypothesis_refs: list[dict[str, Any]] = Field(default_factory=list)
    target_channels: list[str] = Field(default_factory=lambda: ["internal_slack"])
    organization_id: str = "org-1"
    workspace_id: str = "ws-1"
    state_overrides: dict[str, Any] = Field(default_factory=dict)


class RetrospectiveCommand(OpsGraphModel):
    workflow_run_id: str
    incident_id: str
    ops_workspace_id: str = "ops-ws-1"
    current_fact_set_version: int
    confirmed_fact_refs: list[dict[str, Any]] = Field(default_factory=list)
    timeline_refs: list[dict[str, Any]] = Field(default_factory=list)
    resolution_summary: str = ""
    organization_id: str = "org-1"
    workspace_id: str = "ws-1"
    state_overrides: dict[str, Any] = Field(default_factory=dict)


class OpsGraphRunResponse(OpsGraphModel):
    workflow_name: Literal["opsgraph_incident_response", "opsgraph_retrospective"]
    workflow_run_id: str
    workflow_type: str
    current_state: str
    checkpoint_seq: int
    emitted_events: list[str] = Field(default_factory=list)


class OpsGraphWorkflowStateResponse(OpsGraphModel):
    workflow_run_id: str
    workflow_type: str
    current_state: str
    checkpoint_seq: int
    raw_state: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(OpsGraphModel):
    status: Literal["ok"]
    product: Literal["opsgraph"]
