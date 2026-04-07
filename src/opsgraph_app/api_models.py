from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpsGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactSummary(OpsGraphModel):
    fact_id: str = Field(serialization_alias="id")
    fact_type: str
    status: str
    statement: str
    fact_set_version: int
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class IncidentSummary(OpsGraphModel):
    incident_id: str = Field(serialization_alias="id")
    incident_key: str
    title: str
    severity: str
    incident_status: str = Field(serialization_alias="status")
    service_name: str = Field(serialization_alias="service_id")
    opened_at: datetime
    acknowledged_at: datetime | None = None
    current_fact_set_version: int
    latest_workflow_run_id: str | None = None
    updated_at: datetime | None = None


class HypothesisSummary(OpsGraphModel):
    hypothesis_id: str = Field(serialization_alias="id")
    status: str
    rank: int
    confidence: float | None = None
    title: str
    rationale: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime


class RecommendationSummary(OpsGraphModel):
    recommendation_id: str = Field(serialization_alias="id")
    recommendation_type: str = "mitigate"
    title: str
    risk_level: str
    approval_required: bool = Field(serialization_alias="requires_approval")
    status: str
    hypothesis_id: str | None = None
    approval_task_id: str | None = None


class ApprovalTaskSummary(OpsGraphModel):
    approval_task_id: str = Field(serialization_alias="id")
    incident_id: str
    recommendation_id: str | None = None
    status: str
    comment: str | None = None
    created_at: datetime
    updated_at: datetime


class CommsDraftSummary(OpsGraphModel):
    draft_id: str = Field(serialization_alias="id")
    channel: str = Field(serialization_alias="channel_type")
    title: str
    status: str
    fact_set_version: int
    approval_task_id: str | None = None
    published_message_ref: str | None = None
    published_at: datetime | None = None
    created_at: datetime


class TimelineEventSummary(OpsGraphModel):
    event_id: str = Field(serialization_alias="id")
    kind: str
    summary: str
    actor_type: str = "system"
    actor_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuditLogSummary(OpsGraphModel):
    audit_log_id: str = Field(serialization_alias="id")
    incident_id: str
    action_type: str
    actor_type: str = "system"
    actor_user_id: str | None = None
    actor_role: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReplayAdminAuditLogSummary(OpsGraphModel):
    audit_log_id: str = Field(serialization_alias="id")
    workspace_id: str
    action_type: str
    actor_type: str = "system"
    actor_user_id: str | None = None
    actor_role: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PostmortemSummary(OpsGraphModel):
    postmortem_id: str = Field(serialization_alias="id")
    incident_id: str
    status: str
    fact_set_version: int
    artifact_id: str | None = None
    replay_case_id: str | None = None
    finalized_by_user_id: str | None = None
    finalized_at: datetime | None = None
    updated_at: datetime


class ReplayRunSummary(OpsGraphModel):
    replay_run_id: str = Field(serialization_alias="id")
    incident_id: str
    status: str
    model_bundle_version: str
    replay_case_id: str | None = None
    workflow_run_id: str | None = None
    current_state: str | None = None
    error_message: str | None = None
    created_at: datetime


class ReplayQueueProcessResponse(OpsGraphModel):
    workspace_id: str
    queued_count: int
    processed_count: int
    completed_count: int
    failed_count: int
    skipped_count: int
    remaining_queued_count: int
    items: list[ReplayRunSummary] = Field(default_factory=list)


class ReplayWorkerStatusSummary(OpsGraphModel):
    workspace_id: str
    status: str
    iteration: int
    attempted_count: int
    dispatched_count: int
    failed_count: int
    skipped_count: int
    idle_polls: int
    consecutive_failures: int
    remaining_queued_count: int
    error_message: str | None = None
    last_seen_at: datetime


class ReplayWorkerHeartbeatSummary(OpsGraphModel):
    workspace_id: str
    status: str
    iteration: int
    attempted_count: int
    dispatched_count: int
    failed_count: int
    skipped_count: int
    idle_polls: int
    consecutive_failures: int
    remaining_queued_count: int
    error_message: str | None = None
    emitted_at: datetime


class ReplayWorkerAlertSummary(OpsGraphModel):
    level: Literal["healthy", "warning", "critical"]
    headline: str
    detail: str
    latest_failure_status: str | None = None
    latest_failure_at: datetime | None = None
    latest_failure_message: str | None = None


class ReplayWorkerAlertPolicySummary(OpsGraphModel):
    workspace_id: str | None = None
    warning_consecutive_failures: int = 1
    critical_consecutive_failures: int = 3
    default_warning_consecutive_failures: int = 1
    default_critical_consecutive_failures: int = 3
    source: Literal["default", "workspace_override"] = "default"
    updated_at: datetime | None = None


class ReplayWorkerAlertPolicyUpdateCommand(OpsGraphModel):
    warning_consecutive_failures: int
    critical_consecutive_failures: int


class ReplayWorkerMonitorPresetUpsertCommand(OpsGraphModel):
    history_limit: int = 10
    actor_user_id: str | None = None
    request_id: str | None = None
    policy_audit_limit: int = 5
    policy_audit_copy_format: str = "plain"
    policy_audit_include_summary: bool = True

    @model_validator(mode="after")
    def validate_values(self) -> "ReplayWorkerMonitorPresetUpsertCommand":
        if self.history_limit < 1:
            raise ValueError("INVALID_REPLAY_MONITOR_PRESET_HISTORY_LIMIT")
        if self.policy_audit_limit < 1:
            raise ValueError("INVALID_REPLAY_MONITOR_PRESET_AUDIT_LIMIT")
        if self.policy_audit_copy_format not in {"plain", "markdown", "slack"}:
            raise ValueError("INVALID_REPLAY_MONITOR_PRESET_COPY_FORMAT")
        self.actor_user_id = (self.actor_user_id or "").strip() or None
        self.request_id = (self.request_id or "").strip() or None
        return self


class ReplayWorkerMonitorPresetSummary(OpsGraphModel):
    workspace_id: str
    preset_name: str
    history_limit: int = 10
    actor_user_id: str | None = None
    request_id: str | None = None
    policy_audit_limit: int = 5
    policy_audit_copy_format: Literal["plain", "markdown", "slack"] = "plain"
    policy_audit_include_summary: bool = True
    is_default: bool = False
    default_source: Literal["none", "workspace_default", "shift_default"] = "none"
    updated_at: datetime


class ReplayWorkerMonitorPresetDeleteResponse(OpsGraphModel):
    workspace_id: str
    preset_name: str
    deleted: bool = True


class ReplayWorkerMonitorDefaultPresetResponse(OpsGraphModel):
    workspace_id: str
    preset_name: str | None = None
    shift_label: str | None = None
    source: Literal["none", "workspace_default", "shift_default"] = "none"
    updated_at: datetime | None = None
    cleared: bool = False


class ReplayWorkerMonitorShiftWindow(OpsGraphModel):
    shift_label: str
    start_time: str
    end_time: str

    @staticmethod
    def _normalize_hhmm(value: str, *, error_code: str) -> str:
        normalized = str(value or "").strip()
        parts = normalized.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError(error_code)
        hour, minute = (int(parts[0]), int(parts[1]))
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError(error_code)
        return f"{hour:02d}:{minute:02d}"

    @model_validator(mode="after")
    def validate_values(self) -> "ReplayWorkerMonitorShiftWindow":
        self.shift_label = str(self.shift_label or "").strip()
        if not self.shift_label:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_LABEL")
        self.start_time = self._normalize_hhmm(
            self.start_time,
            error_code="INVALID_REPLAY_MONITOR_SHIFT_START_TIME",
        )
        self.end_time = self._normalize_hhmm(
            self.end_time,
            error_code="INVALID_REPLAY_MONITOR_SHIFT_END_TIME",
        )
        if self.start_time == self.end_time:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_WINDOW")
        return self


class ReplayWorkerMonitorShiftDateOverride(OpsGraphModel):
    date: str
    windows: list[ReplayWorkerMonitorShiftWindow] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "ReplayWorkerMonitorShiftDateOverride":
        normalized_date = str(self.date or "").strip()
        try:
            date.fromisoformat(normalized_date)
        except ValueError as exc:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_OVERRIDE_DATE") from exc
        self.date = normalized_date
        self.note = (self.note or "").strip() or None
        seen_labels: set[str] = set()
        for window in self.windows:
            if window.shift_label in seen_labels:
                raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_LABEL")
            seen_labels.add(window.shift_label)
        return self


class ReplayWorkerMonitorShiftDateRangeOverride(OpsGraphModel):
    start_date: str
    end_date: str
    windows: list[ReplayWorkerMonitorShiftWindow] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "ReplayWorkerMonitorShiftDateRangeOverride":
        normalized_start_date = str(self.start_date or "").strip()
        normalized_end_date = str(self.end_date or "").strip()
        try:
            start_value = date.fromisoformat(normalized_start_date)
        except ValueError as exc:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_RANGE_START_DATE") from exc
        try:
            end_value = date.fromisoformat(normalized_end_date)
        except ValueError as exc:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_RANGE_END_DATE") from exc
        if end_value < start_value:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_RANGE")
        self.start_date = normalized_start_date
        self.end_date = normalized_end_date
        self.note = (self.note or "").strip() or None
        seen_labels: set[str] = set()
        for window in self.windows:
            if window.shift_label in seen_labels:
                raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_LABEL")
            seen_labels.add(window.shift_label)
        return self


class ReplayWorkerMonitorShiftScheduleSummary(OpsGraphModel):
    workspace_id: str
    timezone: str = "UTC"
    windows: list[ReplayWorkerMonitorShiftWindow] = Field(default_factory=list)
    date_overrides: list[ReplayWorkerMonitorShiftDateOverride] = Field(default_factory=list)
    date_range_overrides: list[ReplayWorkerMonitorShiftDateRangeOverride] = Field(default_factory=list)
    updated_at: datetime | None = None


class ReplayWorkerMonitorShiftScheduleUpdateCommand(OpsGraphModel):
    timezone: str = "UTC"
    windows: list[ReplayWorkerMonitorShiftWindow] = Field(default_factory=list)
    date_overrides: list[ReplayWorkerMonitorShiftDateOverride] = Field(default_factory=list)
    date_range_overrides: list[ReplayWorkerMonitorShiftDateRangeOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_values(self) -> "ReplayWorkerMonitorShiftScheduleUpdateCommand":
        self.timezone = str(self.timezone or "").strip() or "UTC"
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_TIMEZONE") from exc
        seen_labels: set[str] = set()
        for window in self.windows:
            if window.shift_label in seen_labels:
                raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_LABEL")
            seen_labels.add(window.shift_label)
        seen_dates: set[str] = set()
        for override in self.date_overrides:
            if override.date in seen_dates:
                raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_OVERRIDE_DATE")
            seen_dates.add(override.date)
        sorted_ranges = sorted(
            self.date_range_overrides,
            key=lambda item: (item.start_date, item.end_date),
        )
        previous_end: date | None = None
        for override in sorted_ranges:
            current_start = date.fromisoformat(override.start_date)
            current_end = date.fromisoformat(override.end_date)
            if previous_end is not None and current_start <= previous_end:
                raise ValueError("INVALID_REPLAY_MONITOR_SHIFT_OVERLAPPING_RANGE_OVERRIDE")
            previous_end = current_end
        return self


class ReplayWorkerMonitorShiftScheduleDeleteResponse(OpsGraphModel):
    workspace_id: str
    cleared: bool = True


class ReplayWorkerMonitorResolvedShiftResponse(OpsGraphModel):
    workspace_id: str
    timezone: str | None = None
    evaluated_at: datetime
    shift_label: str | None = None
    source: Literal["none", "schedule", "date_override", "date_range_override"] = "none"
    matched_window: ReplayWorkerMonitorShiftWindow | None = None
    override_date: str | None = None
    override_range_start_date: str | None = None
    override_range_end_date: str | None = None
    override_note: str | None = None
    updated_at: datetime | None = None


class ReplayWorkerStatusResponse(OpsGraphModel):
    workspace_id: str | None = None
    current: ReplayWorkerStatusSummary | None = None
    history: list[ReplayWorkerHeartbeatSummary] = Field(default_factory=list)
    alert: ReplayWorkerAlertSummary | None = None
    policy: ReplayWorkerAlertPolicySummary | None = None


class ReplayCaseSummary(OpsGraphModel):
    replay_case_id: str = Field(serialization_alias="id")
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


class ReplaySemanticCheckSummary(OpsGraphModel):
    check_name: str
    matched: bool
    expected_summary: str | None = None
    actual_summary: str | None = None
    detail: str


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
    node_match_rate: float = 0.0
    bundle_mismatch_count: int = 0
    version_mismatch_count: int = 0
    summary_mismatch_count: int = 0
    missing_baseline_node_count: int = 0
    missing_replay_node_count: int = 0
    state_mismatch_count: int = 0
    checkpoint_mismatch_count: int = 0
    latency_regression_count: int = 0
    latency_improvement_count: int = 0
    latency_regression_total_ms: int = 0
    avg_latency_delta_ms: float | None = None
    max_latency_delta_ms: int | None = None
    semantic_check_count: int = 0
    semantic_mismatch_count: int = 0
    semantic_match_rate: float | None = None
    service_id_mismatch_count: int = 0
    incident_status_mismatch_count: int = 0
    fact_set_version_mismatch_count: int = 0
    top_hypothesis_expected_count: int = 0
    top_hypothesis_actual_count: int = 0
    top_hypothesis_hit_count: int = 0
    top_hypothesis_hit_rate: float | None = None
    recommendation_expected_count: int = 0
    recommendation_actual_count: int = 0
    recommendation_match_count: int = 0
    recommendation_match_rate: float | None = None
    comms_expected_count: int = 0
    comms_actual_count: int = 0
    comms_match_count: int = 0
    comms_match_rate: float | None = None
    postmortem_present_expected: bool = False
    postmortem_present_actual: bool = False
    postmortem_markdown_matched: bool | None = None
    semantic_checks: list[ReplaySemanticCheckSummary] = Field(default_factory=list)
    mismatches: list[str] = Field(default_factory=list)
    baseline_final_state: str | None = None
    replay_final_state: str | None = None
    baseline_checkpoint_seq: int | None = None
    replay_checkpoint_seq: int | None = None
    node_diffs: list[ReplayNodeDiffSummary] = Field(default_factory=list)
    report_artifact_path: str | None = None
    markdown_report_path: str | None = None
    csv_report_path: str | None = None
    created_at: datetime


class SignalSummary(OpsGraphModel):
    signal_id: str = Field(serialization_alias="id")
    source: str
    status: str
    title: str
    dedupe_key: str
    fired_at: datetime


class IncidentWorkspaceResponse(OpsGraphModel):
    incident: IncidentSummary
    signals: list[SignalSummary] = Field(default_factory=list)
    confirmed_facts: list[FactSummary] = Field(default_factory=list, serialization_alias="facts")
    hypotheses: list[HypothesisSummary] = Field(default_factory=list)
    recommendations: list[RecommendationSummary] = Field(default_factory=list)
    approval_tasks: list[ApprovalTaskSummary] = Field(default_factory=list)
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
    accepted_signals: int = 1
    workflow_run_id: str | None = None


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
    delivery_state: Literal["accepted", "published", "failed"] | None = None
    delivery_confirmed: bool = False
    provider_delivery_status: str | None = None
    published_at: datetime | None = None
    delivery_error: dict[str, Any] | None = None


class ApprovalDecisionCommand(OpsGraphModel):
    decision: Literal["approve", "reject"]
    comment: str = ""
    execute_recommendation: bool = False
    publish_linked_drafts: bool = False
    linked_draft_ids: list[str] = Field(default_factory=list)
    expected_fact_set_version: int | None = None


class ApprovalDecisionResponse(OpsGraphModel):
    approval_task: ApprovalTaskSummary
    recommendation: RecommendationDecisionResponse | None = None
    published_drafts: list[CommsPublishResponse] = Field(default_factory=list)


class RecommendationDecisionCommand(OpsGraphModel):
    decision: Literal["approve", "reject", "mark_executed"]
    comment: str = ""
    approval_task_id: str | None = None
    expected_updated_at: datetime | None = None


class RecommendationDecisionResponse(OpsGraphModel):
    recommendation_id: str
    status: str
    approval_task_id: str | None = None
    approval_status: str | None = None


class ResolveIncidentCommand(OpsGraphModel):
    resolution_summary: str
    root_cause_fact_ids: list[str] = Field(default_factory=list)
    expected_updated_at: datetime | None = None


class CloseIncidentCommand(OpsGraphModel):
    close_reason: str
    expected_updated_at: datetime | None = None


class PostmortemFinalizeCommand(OpsGraphModel):
    finalized_by_user_id: str = "incident-commander-demo"
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
    context_bundle_id: str | None = None
    context_bundle_summary: str | None = None
    context_missing_sources: list[str] = Field(default_factory=list)
    context_bundle_refs: list[dict[str, Any]] = Field(default_factory=list)
    current_fact_set_version: int = 1
    service_id: str | None = None
    confirmed_fact_refs: list[dict[str, Any]] = Field(default_factory=list)
    top_hypothesis_refs: list[dict[str, Any]] = Field(default_factory=list)
    investigation_memory_context: list[dict[str, Any]] = Field(default_factory=list)
    recommendation_memory_context: list[dict[str, Any]] = Field(default_factory=list)
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
    postmortem_memory_context: list[dict[str, Any]] = Field(default_factory=list)
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


class RuntimeCapability(OpsGraphModel):
    requested_mode: str
    effective_mode: str
    backend_id: str
    fallback_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeAuthSummary(OpsGraphModel):
    mode: Literal["demo_compatible", "strict"]
    source: Literal["product_compat", "shared_delegated"] | None = None
    header_fallback_enabled: bool = False
    demo_seed_enabled: bool = False
    bootstrap_admin_configured: bool = False
    bootstrap_organization_slug: str | None = None


class RuntimeCapabilitiesResponse(OpsGraphModel):
    product: Literal["opsgraph"] = "opsgraph"
    model_provider: RuntimeCapability
    tooling: dict[str, RuntimeCapability] = Field(default_factory=dict)
    auth: RuntimeAuthSummary | None = None
    runtime_provider_alert: "RuntimeProviderAlertSummary | None" = None
    remote_provider_smoke_alert: "RemoteProviderSmokeAlertSummary | None" = None
    replay_worker: ReplayWorkerStatusSummary | None = None
    replay_worker_history: list[ReplayWorkerHeartbeatSummary] = Field(default_factory=list)
    replay_worker_alert: ReplayWorkerAlertSummary | None = None
    replay_worker_alert_policy: ReplayWorkerAlertPolicySummary | None = None


class ReplayQualitySummary(OpsGraphModel):
    workspace_id: str
    incident_id: str | None = None
    incident_count: int = 0
    replay_case_count: int = 0
    replay_case_expected_output_count: int = 0
    replay_case_expected_output_coverage_rate: float = 0.0
    baseline_count: int = 0
    baseline_incident_coverage_count: int = 0
    baseline_coverage_rate: float = 0.0
    evaluation_count: int = 0
    matched_evaluation_count: int = 0
    mismatched_evaluation_count: int = 0
    replay_pass_rate: float = 0.0
    avg_replay_score: float | None = None
    semantic_evaluation_count: int = 0
    avg_semantic_match_rate: float | None = None
    avg_top_hypothesis_hit_rate: float | None = None
    avg_recommendation_match_rate: float | None = None
    avg_comms_match_rate: float | None = None
    latest_report_id: str | None = None
    latest_report_created_at: datetime | None = None


class RuntimeProviderAlertItem(OpsGraphModel):
    capability_name: str
    level: Literal["warning", "critical"]
    requested_mode: str
    effective_mode: str
    backend_id: str
    strict_remote_required: bool = False
    reason_code: str
    detail: str


class RuntimeProviderAlertSummary(OpsGraphModel):
    level: Literal["healthy", "warning", "critical"] = "healthy"
    headline: str
    detail: str
    active_alert_count: int = 0
    alerts: list[RuntimeProviderAlertItem] = Field(default_factory=list)


class RemoteProviderSmokeCommand(OpsGraphModel):
    providers: list[str] = Field(default_factory=list)
    include_write: bool = False
    allow_write: bool = False
    require_configured: bool = False
    service_id: str = "checkout-api"
    incident_id: str = "incident-1"
    limit: int = 3
    search_query: str = "checkout api"
    runbook_query: str = "rollback elevated 5xx"
    draft_id: str = "draft-1"
    channel_type: str = "internal_slack"
    title: str = "OpsGraph remote provider smoke"
    body_markdown: str = "Smoke validation for remote provider delivery."
    fact_set_version: int = 1


class RemoteProviderSmokeSummary(OpsGraphModel):
    success_count: int
    skipped_count: int
    failed_count: int


class RemoteProviderSmokeResult(OpsGraphModel):
    provider: str
    status: Literal["success", "skipped", "failed"]
    reason: str | None = None
    capability: RuntimeCapability
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


class RemoteProviderSmokeResponse(OpsGraphModel):
    providers: list[str] = Field(default_factory=list)
    summary: RemoteProviderSmokeSummary
    results: list[RemoteProviderSmokeResult] = Field(default_factory=list)
    exit_code: int
    diagnostic_run_id: str | None = None
    created_at: datetime | None = None


class RemoteProviderSmokeRunRecord(OpsGraphModel):
    diagnostic_run_id: str
    actor_type: str = "system"
    actor_user_id: str | None = None
    actor_role: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response: RemoteProviderSmokeResponse
    created_at: datetime


class RemoteProviderSmokeProviderSummary(OpsGraphModel):
    provider: str
    run_count: int = 0
    success_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    consecutive_failure_count: int = 0
    consecutive_non_success_count: int = 0
    last_status: Literal["success", "skipped", "failed"] | None = None
    last_reason: str | None = None
    last_seen_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_skipped_at: datetime | None = None
    last_diagnostic_run_id: str | None = None
    latest_effective_mode: str | None = None
    latest_backend_id: str | None = None
    latest_strict_remote_required: bool = False


class RemoteProviderSmokeHistorySummary(OpsGraphModel):
    scanned_run_count: int = 0
    provider_count: int = 0
    providers: list[RemoteProviderSmokeProviderSummary] = Field(default_factory=list)


class RemoteProviderSmokeAlertItem(OpsGraphModel):
    provider: str
    level: Literal["warning", "critical"]
    reason_code: str
    detail: str
    last_status: Literal["success", "skipped", "failed"] | None = None
    last_reason: str | None = None
    last_seen_at: datetime | None = None
    last_diagnostic_run_id: str | None = None
    consecutive_failure_count: int = 0
    consecutive_non_success_count: int = 0


class RemoteProviderSmokeAlertSummary(OpsGraphModel):
    level: Literal["healthy", "warning", "critical"] = "healthy"
    headline: str
    detail: str
    active_alert_count: int = 0
    alerts: list[RemoteProviderSmokeAlertItem] = Field(default_factory=list)


class HealthRuntimeSummary(OpsGraphModel):
    model_provider_mode: str
    model_backend_id: str
    tooling_profile: Literal["product-runtime"] = "product-runtime"
    tooling_modes: dict[str, str] = Field(default_factory=dict)
    tooling_backends: dict[str, str] = Field(default_factory=dict)
    auth_mode: Literal["demo_compatible", "strict"] | None = None
    auth_source: Literal["product_compat", "shared_delegated"] | None = None
    auth_header_fallback_enabled: bool = False
    auth_demo_seed_enabled: bool = False
    auth_bootstrap_admin_configured: bool = False
    runtime_provider_alert_level: Literal["healthy", "warning", "critical"] | None = None
    runtime_provider_alert_count: int = 0
    remote_provider_smoke_alert_level: Literal["healthy", "warning", "critical"] | None = None
    remote_provider_smoke_alert_count: int = 0
    replay_worker_status: str | None = None
    replay_worker_last_seen_at: datetime | None = None
    replay_worker_workspace_id: str | None = None
    replay_worker_remaining_queued_count: int | None = None
    replay_worker_alert_level: Literal["healthy", "warning", "critical"] | None = None


class HealthResponse(OpsGraphModel):
    status: Literal["ok"]
    product: Literal["opsgraph"]
    runtime_summary: HealthRuntimeSummary | None = None
