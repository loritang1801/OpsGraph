from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from .api_models import (
    ApprovalDecisionCommand,
    ApprovalDecisionResponse,
    ApprovalTaskSummary,
    AlertIngestCommand,
    AlertIngestResponse,
    CloseIncidentCommand,
    CommsDraftSummary,
    CommsPublishCommand,
    CommsPublishResponse,
    FactCreateCommand,
    FactMutationResponse,
    FactRetractCommand,
    HealthResponse,
    HealthRuntimeSummary,
    HypothesisDecisionCommand,
    HypothesisDecisionResponse,
    HypothesisSummary,
    IncidentResponseCommand,
    IncidentSummary,
    IncidentWorkspaceResponse,
    OpsGraphRunResponse,
    OpsGraphWorkflowStateResponse,
    PostmortemFinalizeCommand,
    PostmortemSummary,
    ReplayCaseDetail,
    ReplayCaseSummary,
    ReplayBaselineCaptureCommand,
    ReplayBaselineSummary,
    ReplayAdminAuditLogSummary,
    ReplayEvaluationCommand,
    ReplayEvaluationSummary,
    ReplayNodeDiffSummary,
    ReplayNodeSummary,
    ReplayQueueProcessResponse,
    ReplayWorkerAlertSummary,
    ReplayWorkerAlertPolicySummary,
    ReplayWorkerAlertPolicyUpdateCommand,
    ReplayWorkerMonitorDefaultPresetResponse,
    ReplayWorkerMonitorResolvedShiftResponse,
    ReplayWorkerMonitorShiftDateRangeOverride,
    ReplayWorkerMonitorPresetDeleteResponse,
    ReplayWorkerMonitorPresetSummary,
    ReplayWorkerMonitorPresetUpsertCommand,
    ReplayWorkerMonitorShiftScheduleDeleteResponse,
    ReplayWorkerMonitorShiftScheduleSummary,
    ReplayWorkerMonitorShiftScheduleUpdateCommand,
    ReplayWorkerMonitorShiftWindow,
    ReplayWorkerStatusResponse,
    RecommendationDecisionCommand,
    RecommendationDecisionResponse,
    ReplayRunCommand,
    ReplayStatusCommand,
    ReplayRunSummary,
    RuntimeCapabilitiesResponse,
    RecommendationSummary,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    SeverityOverrideCommand,
)
from .repository import OpsGraphRepository
from .replay_fixtures import seed_incident_response_replay_fixtures
from .replay_reports import write_replay_report_artifacts
from .shared_runtime import load_shared_agent_platform
from .tool_adapters import describe_opsgraph_product_tool_capabilities

CommandT = TypeVar("CommandT", IncidentResponseCommand, RetrospectiveCommand)


class OpsGraphAppService:
    def __init__(
        self,
        workflow_api_service,
        repository: OpsGraphRepository,
        runtime_stores=None,
        auth_service=None,
        shared_platform=None,
        workflow_registry=None,
        prompt_service=None,
        replay_fixture_store=None,
        replay_worker_alert_warning_consecutive_failures: int = 1,
        replay_worker_alert_critical_consecutive_failures: int = 3,
    ) -> None:
        (
            self.replay_worker_alert_warning_consecutive_failures,
            self.replay_worker_alert_critical_consecutive_failures,
        ) = self._validate_replay_worker_alert_thresholds(
            warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
            critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        )
        self.workflow_api_service = workflow_api_service
        self.repository = repository
        self.runtime_stores = runtime_stores
        self.auth_service = auth_service
        self.shared_platform = shared_platform
        self.workflow_registry = workflow_registry
        self.prompt_service = prompt_service
        self.replay_fixture_store = replay_fixture_store

    @staticmethod
    def _validate_replay_worker_alert_thresholds(
        *,
        warning_consecutive_failures: int,
        critical_consecutive_failures: int,
    ) -> tuple[int, int]:
        if warning_consecutive_failures < 1:
            raise ValueError("INVALID_REPLAY_WORKER_ALERT_WARNING_THRESHOLD")
        if critical_consecutive_failures < warning_consecutive_failures:
            raise ValueError("INVALID_REPLAY_WORKER_ALERT_CRITICAL_THRESHOLD")
        return warning_consecutive_failures, critical_consecutive_failures

    @staticmethod
    def _coerce_command(command: CommandT | dict[str, Any], model_type: type[CommandT]) -> CommandT:
        if isinstance(command, model_type):
            return command
        if isinstance(command, dict):
            return model_type.model_validate(command)
        raise TypeError(f"Expected {model_type.__name__} or dict, got {type(command).__name__}")

    @staticmethod
    def _to_run_response(result) -> OpsGraphRunResponse:
        return OpsGraphRunResponse.model_validate(result.model_dump())

    def _run_registered_workflow(
        self,
        *,
        workflow_name: str,
        workflow_run_id: str,
        input_payload: dict[str, Any],
        state_overrides: dict[str, Any] | None = None,
    ) -> tuple[OpsGraphRunResponse, Any]:
        if self.workflow_registry is None:
            raise ValueError("WORKFLOW_REGISTRY_UNAVAILABLE")
        definition = self.workflow_registry.get(workflow_name)
        initial_state = definition.initial_state_builder(
            workflow_run_id,
            input_payload,
            dict(state_overrides or {}),
        )
        run_result = self.workflow_api_service.execution_service.run_workflow(
            workflow_run_id=workflow_run_id,
            workflow_type=definition.workflow_type,
            initial_state=initial_state,
            steps=definition.steps,
            source_builders=definition.source_builders,
        )
        response = OpsGraphRunResponse(
            workflow_name=workflow_name,
            workflow_run_id=run_result.workflow_run_id,
            workflow_type=run_result.workflow_type,
            current_state=str(run_result.final_state.get("current_state", "")),
            checkpoint_seq=int(run_result.final_state.get("checkpoint_seq", 0)),
            emitted_events=[event for step in run_result.step_results for event in step.emitted_events],
        )
        return response, run_result

    @staticmethod
    def _step_structured_output(run_result, node_name: str) -> dict[str, Any]:
        for step_result in run_result.step_results:
            if step_result.trace.node_name == node_name:
                return (
                    dict(step_result.agent_output.structured_output)
                    if isinstance(step_result.agent_output.structured_output, dict)
                    else {}
                )
        return {}

    @staticmethod
    def _hash_request_payload(payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_audit_context(auth_context, *, request_id: str | None = None) -> dict[str, object] | None:
        if auth_context is None and request_id is None:
            return None
        context: dict[str, object] = {
            "actor_type": "system" if auth_context is None else "user",
        }
        if auth_context is not None:
            context["actor_user_id"] = getattr(auth_context, "user_id", None)
            context["actor_role"] = getattr(auth_context, "role", None)
            context["session_id"] = getattr(auth_context, "session_id", None)
        if request_id is not None:
            context["request_id"] = request_id
        return context

    def _persist_workflow_state_patch(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        state_patch: dict[str, Any],
    ) -> None:
        if self.runtime_stores is None or not hasattr(self.runtime_stores, "state_store"):
            return
        state_store = getattr(self.runtime_stores, "state_store", None)
        if state_store is None:
            return
        try:
            existing_record = state_store.load(workflow_run_id)
        except Exception:
            return
        shared_platform = self.shared_platform or load_shared_agent_platform()
        merged_state = dict(existing_record.state)
        merged_state.update(state_patch)
        checkpoint_seq = int(merged_state.get("checkpoint_seq", existing_record.checkpoint_seq))
        state_store.save(
            shared_platform.WorkflowStateRecord(
                workflow_run_id=workflow_run_id,
                workflow_type=workflow_type or existing_record.workflow_type,
                checkpoint_seq=checkpoint_seq,
                state=merged_state,
                updated_at=datetime.now(UTC),
            )
        )

    def _sync_incident_workflow_state(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        incident_id: str,
    ) -> IncidentWorkspaceResponse:
        workspace = self.repository.get_incident_workspace(incident_id)
        self._persist_workflow_state_patch(
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            state_patch={
                "incident_status": workspace.incident.incident_status,
                "severity": workspace.incident.severity,
                "service_id": workspace.incident.service_name,
                "title": workspace.incident.title,
                "current_fact_set_version": workspace.incident.current_fact_set_version,
                "hypothesis_ids": [item.hypothesis_id for item in workspace.hypotheses],
                "top_hypothesis_ids": [
                    item.hypothesis_id for item in workspace.hypotheses[:3]
                ],
                "recommendation_ids": [
                    item.recommendation_id for item in workspace.recommendations
                ],
                "pending_approval_task_ids": [
                    item.approval_task_id
                    for item in workspace.approval_tasks
                    if item.status == "pending"
                ],
                "comms_draft_ids": [item.draft_id for item in workspace.comms_drafts],
                "publish_ready_draft_ids": [
                    item.draft_id
                    for item in workspace.comms_drafts
                    if item.status == "draft"
                ],
            },
        )
        return workspace

    def _sync_retrospective_workflow_state(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        incident_id: str,
    ) -> tuple[IncidentWorkspaceResponse, PostmortemSummary]:
        workspace = self.repository.get_incident_workspace(incident_id)
        postmortem = self.repository.get_postmortem(incident_id)
        self._persist_workflow_state_patch(
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            state_patch={
                "incident_status": workspace.incident.incident_status,
                "severity": workspace.incident.severity,
                "service_id": workspace.incident.service_name,
                "title": workspace.incident.title,
                "current_fact_set_version": workspace.incident.current_fact_set_version,
                "postmortem_id": postmortem.postmortem_id,
                "replay_case_id": postmortem.replay_case_id,
                "postmortem_status": postmortem.status,
                "postmortem_artifact_id": postmortem.artifact_id,
            },
        )
        return workspace, postmortem

    def _load_idempotent_response(self, *, operation: str, idempotency_key: str | None, request_payload: dict[str, Any], model_type):
        if not idempotency_key:
            return None
        payload = self.repository.load_idempotency_response(
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=self._hash_request_payload(request_payload),
        )
        if payload is None:
            return None
        return model_type.model_validate(payload)

    def _store_idempotent_response(
        self,
        *,
        operation: str,
        idempotency_key: str | None,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> None:
        if not idempotency_key:
            return
        self.repository.store_idempotency_response(
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=self._hash_request_payload(request_payload),
            response_payload=response_payload,
        )

    def list_workflows(self):
        return self.workflow_api_service.list_workflows()

    def get_runtime_capabilities(self) -> RuntimeCapabilitiesResponse:
        model_gateway = getattr(self.workflow_api_service.execution_service, "model_gateway", None)
        tool_executor = getattr(self.workflow_api_service.execution_service, "tool_executor", None)
        replay_worker_status = self.repository.get_replay_worker_status()
        replay_worker_history = self.repository.list_replay_worker_history(limit=5)
        replay_worker_workspace_id = (
            replay_worker_status.workspace_id
            if replay_worker_status is not None
            else (replay_worker_history[0].workspace_id if replay_worker_history else None)
        )
        replay_worker_policy = self._resolve_replay_worker_alert_policy(replay_worker_workspace_id)
        replay_worker_alert = self._build_replay_worker_alert(
            current=replay_worker_status,
            latest_failure=self._latest_replay_worker_failure(replay_worker_history),
            policy=replay_worker_policy,
        )
        model_provider = (
            model_gateway.describe_capability()
            if model_gateway is not None and hasattr(model_gateway, "describe_capability")
            else {
                "requested_mode": "unknown",
                "effective_mode": "unknown",
                "backend_id": "unknown",
                "fallback_reason": None,
                "details": {},
            }
        )
        return RuntimeCapabilitiesResponse.model_validate(
            {
                "product": "opsgraph",
                "model_provider": model_provider,
                "tooling": describe_opsgraph_product_tool_capabilities(tool_executor),
                "replay_worker": (
                    replay_worker_status.model_dump(mode="json")
                    if replay_worker_status is not None
                    else None
                ),
                "replay_worker_history": [
                    item.model_dump(mode="json") for item in replay_worker_history
                ],
                "replay_worker_alert": (
                    replay_worker_alert.model_dump(mode="json")
                    if replay_worker_alert is not None
                    else None
                ),
                "replay_worker_alert_policy": replay_worker_policy.model_dump(mode="json"),
            }
        )

    def get_health_status(self) -> HealthResponse:
        capabilities = self.get_runtime_capabilities()
        return HealthResponse(
            status="ok",
            product="opsgraph",
            runtime_summary=HealthRuntimeSummary(
                model_provider_mode=capabilities.model_provider.effective_mode,
                model_backend_id=capabilities.model_provider.backend_id,
                tooling_modes={
                    capability_name: capability.effective_mode
                    for capability_name, capability in capabilities.tooling.items()
                },
                tooling_backends={
                    capability_name: capability.backend_id
                    for capability_name, capability in capabilities.tooling.items()
                },
                replay_worker_status=(
                    capabilities.replay_worker.status
                    if capabilities.replay_worker is not None
                    else None
                ),
                replay_worker_last_seen_at=(
                    capabilities.replay_worker.last_seen_at
                    if capabilities.replay_worker is not None
                    else None
                ),
                replay_worker_workspace_id=(
                    capabilities.replay_worker.workspace_id
                    if capabilities.replay_worker is not None
                    else None
                ),
                replay_worker_remaining_queued_count=(
                    capabilities.replay_worker.remaining_queued_count
                    if capabilities.replay_worker is not None
                    else None
                ),
                replay_worker_alert_level=(
                    capabilities.replay_worker_alert.level
                    if capabilities.replay_worker_alert is not None
                    and (
                        capabilities.replay_worker is not None
                        or bool(capabilities.replay_worker_history)
                    )
                    else None
                ),
            ),
        )

    def get_replay_worker_status(
        self,
        *,
        workspace_id: str | None = None,
        history_limit: int = 10,
    ) -> ReplayWorkerStatusResponse:
        if history_limit < 1:
            raise ValueError("INVALID_REPLAY_WORKER_HISTORY_LIMIT")
        current = self.repository.get_replay_worker_status(workspace_id)
        history = self.repository.list_replay_worker_history(
            workspace_id,
            limit=history_limit,
        )
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            if current is not None:
                resolved_workspace_id = current.workspace_id
            elif history:
                resolved_workspace_id = history[0].workspace_id
        policy = self._resolve_replay_worker_alert_policy(resolved_workspace_id)
        latest_failure = self._latest_replay_worker_failure(history)
        alert = self._build_replay_worker_alert(
            current=current,
            latest_failure=latest_failure,
            policy=policy,
        )
        return ReplayWorkerStatusResponse(
            workspace_id=resolved_workspace_id,
            current=current,
            history=history,
            alert=alert,
            policy=policy,
        )

    def get_replay_worker_alert_policy(self, workspace_id: str) -> ReplayWorkerAlertPolicySummary:
        return self._resolve_replay_worker_alert_policy(workspace_id)

    @staticmethod
    def _normalize_replay_worker_monitor_preset_name(preset_name: str) -> str:
        normalized = str(preset_name).strip()
        if not normalized:
            raise ValueError("INVALID_REPLAY_MONITOR_PRESET_NAME")
        return normalized

    @staticmethod
    def _normalize_replay_worker_monitor_shift_label(shift_label: str | None) -> str | None:
        normalized = str(shift_label or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_replay_worker_monitor_resolved_at(
        evaluated_at: datetime | None,
    ) -> datetime:
        if evaluated_at is None:
            return datetime.now(UTC)
        if evaluated_at.tzinfo is None:
            return evaluated_at.replace(tzinfo=UTC)
        return evaluated_at.astimezone(UTC)

    @staticmethod
    def _parse_shift_window_minutes(window: ReplayWorkerMonitorShiftWindow) -> tuple[int, int]:
        start_hour, start_minute = (int(part) for part in window.start_time.split(":"))
        end_hour, end_minute = (int(part) for part in window.end_time.split(":"))
        return (start_hour * 60 + start_minute, end_hour * 60 + end_minute)

    @staticmethod
    def _match_shift_window(
        windows: list[ReplayWorkerMonitorShiftWindow],
        *,
        minute_of_day: int,
    ) -> ReplayWorkerMonitorShiftWindow | None:
        for window in windows:
            start_minutes, end_minutes = OpsGraphAppService._parse_shift_window_minutes(window)
            matches = (
                start_minutes <= minute_of_day < end_minutes
                if start_minutes < end_minutes
                else (minute_of_day >= start_minutes or minute_of_day < end_minutes)
            )
            if matches:
                return window
        return None

    def list_replay_worker_monitor_presets(
        self,
        workspace_id: str,
        *,
        shift_label: str | None = None,
    ) -> list[ReplayWorkerMonitorPresetSummary]:
        return self.repository.list_replay_worker_monitor_presets(
            workspace_id,
            shift_label=self._normalize_replay_worker_monitor_shift_label(shift_label),
        )

    def upsert_replay_worker_monitor_preset(
        self,
        workspace_id: str,
        preset_name: str,
        command: ReplayWorkerMonitorPresetUpsertCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorPresetSummary:
        normalized_preset_name = self._normalize_replay_worker_monitor_preset_name(preset_name)
        payload = self._coerce_command(command, ReplayWorkerMonitorPresetUpsertCommand)
        response = self.repository.upsert_replay_worker_monitor_preset(
            workspace_id=workspace_id,
            preset_name=normalized_preset_name,
            history_limit=payload.history_limit,
            actor_user_id=payload.actor_user_id,
            request_id=payload.request_id,
            policy_audit_limit=payload.policy_audit_limit,
            policy_audit_copy_format=payload.policy_audit_copy_format,
            policy_audit_include_summary=payload.policy_audit_include_summary,
        )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.upsert_worker_monitor_preset",
            subject_type="replay_worker_monitor_preset",
            subject_id=f"{workspace_id}:{normalized_preset_name}",
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload={
                "preset_name": normalized_preset_name,
                **payload.model_dump(mode="json"),
            },
            result_payload=response.model_dump(mode="json"),
        )
        return response

    def get_replay_worker_monitor_default_preset(
        self,
        workspace_id: str,
        *,
        shift_label: str | None = None,
    ) -> ReplayWorkerMonitorDefaultPresetResponse:
        normalized_shift_label = self._normalize_replay_worker_monitor_shift_label(shift_label)
        response = self.repository.get_replay_worker_monitor_default_preset(
            workspace_id,
            shift_label=normalized_shift_label,
        )
        if response is None:
            return ReplayWorkerMonitorDefaultPresetResponse(
                workspace_id=workspace_id,
                preset_name=None,
                shift_label=normalized_shift_label,
                source="none",
                updated_at=None,
                cleared=False,
            )
        return response

    def set_replay_worker_monitor_default_preset(
        self,
        workspace_id: str,
        preset_name: str,
        *,
        shift_label: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorDefaultPresetResponse:
        normalized_preset_name = self._normalize_replay_worker_monitor_preset_name(preset_name)
        normalized_shift_label = self._normalize_replay_worker_monitor_shift_label(shift_label)
        response = self.repository.set_replay_worker_monitor_default_preset(
            workspace_id,
            normalized_preset_name,
            shift_label=normalized_shift_label,
        )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.set_worker_monitor_default_preset",
            subject_type="replay_worker_monitor_preset_default",
            subject_id=workspace_id,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload={
                "preset_name": normalized_preset_name,
                "shift_label": normalized_shift_label,
            },
            result_payload=response.model_dump(mode="json"),
        )
        return response

    def clear_replay_worker_monitor_default_preset(
        self,
        workspace_id: str,
        *,
        shift_label: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorDefaultPresetResponse:
        normalized_shift_label = self._normalize_replay_worker_monitor_shift_label(shift_label)
        response = self.repository.clear_replay_worker_monitor_default_preset(
            workspace_id,
            shift_label=normalized_shift_label,
        )
        if response is None:
            response = ReplayWorkerMonitorDefaultPresetResponse(
                workspace_id=workspace_id,
                preset_name=None,
                shift_label=normalized_shift_label,
                source="none",
                updated_at=None,
                cleared=True,
            )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.clear_worker_monitor_default_preset",
            subject_type="replay_worker_monitor_preset_default",
            subject_id=workspace_id,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload={"shift_label": normalized_shift_label},
            result_payload=response.model_dump(mode="json"),
        )
        return response

    def get_replay_worker_monitor_shift_schedule(
        self,
        workspace_id: str,
    ) -> ReplayWorkerMonitorShiftScheduleSummary:
        response = self.repository.get_replay_worker_monitor_shift_schedule(workspace_id)
        if response is None:
            return ReplayWorkerMonitorShiftScheduleSummary(
                workspace_id=workspace_id,
                timezone="UTC",
                windows=[],
                updated_at=None,
            )
        return response

    def update_replay_worker_monitor_shift_schedule(
        self,
        workspace_id: str,
        command: ReplayWorkerMonitorShiftScheduleUpdateCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorShiftScheduleSummary:
        payload = self._coerce_command(command, ReplayWorkerMonitorShiftScheduleUpdateCommand)
        response = self.repository.upsert_replay_worker_monitor_shift_schedule(
            workspace_id,
            timezone=payload.timezone,
            windows=payload.windows,
            date_overrides=payload.date_overrides,
            date_range_overrides=payload.date_range_overrides,
        )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.update_worker_monitor_shift_schedule",
            subject_type="replay_worker_monitor_shift_schedule",
            subject_id=workspace_id,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload=payload.model_dump(mode="json"),
            result_payload=response.model_dump(mode="json"),
        )
        return response

    def clear_replay_worker_monitor_shift_schedule(
        self,
        workspace_id: str,
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorShiftScheduleDeleteResponse:
        response = self.repository.clear_replay_worker_monitor_shift_schedule(workspace_id)
        if response is None:
            response = ReplayWorkerMonitorShiftScheduleDeleteResponse(
                workspace_id=workspace_id,
                cleared=True,
            )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.clear_worker_monitor_shift_schedule",
            subject_type="replay_worker_monitor_shift_schedule",
            subject_id=workspace_id,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload={},
            result_payload=response.model_dump(mode="json"),
        )
        return response

    def resolve_replay_worker_monitor_shift_label(
        self,
        workspace_id: str,
        *,
        evaluated_at: datetime | None = None,
    ) -> ReplayWorkerMonitorResolvedShiftResponse:
        schedule = self.repository.get_replay_worker_monitor_shift_schedule(workspace_id)
        resolved_at = self._normalize_replay_worker_monitor_resolved_at(evaluated_at)
        if schedule is None or not schedule.windows:
            return ReplayWorkerMonitorResolvedShiftResponse(
                workspace_id=workspace_id,
                timezone=schedule.timezone if schedule is not None else None,
                evaluated_at=resolved_at,
                shift_label=None,
                source="none",
                matched_window=None,
                updated_at=schedule.updated_at if schedule is not None else None,
            )
        local_dt = resolved_at.astimezone(ZoneInfo(schedule.timezone))
        minute_of_day = local_dt.hour * 60 + local_dt.minute
        local_date = local_dt.date().isoformat()
        override = next(
            (item for item in schedule.date_overrides if item.date == local_date),
            None,
        )
        if override is not None:
            matched_window = self._match_shift_window(
                override.windows,
                minute_of_day=minute_of_day,
            )
            if matched_window is not None:
                return ReplayWorkerMonitorResolvedShiftResponse(
                    workspace_id=workspace_id,
                    timezone=schedule.timezone,
                    evaluated_at=resolved_at,
                    shift_label=matched_window.shift_label,
                    source="date_override",
                    matched_window=matched_window,
                    override_date=override.date,
                    override_range_start_date=None,
                    override_range_end_date=None,
                    override_note=override.note,
                    updated_at=schedule.updated_at,
                )
            return ReplayWorkerMonitorResolvedShiftResponse(
                workspace_id=workspace_id,
                timezone=schedule.timezone,
                evaluated_at=resolved_at,
                shift_label=None,
                source="date_override",
                matched_window=None,
                override_date=override.date,
                override_range_start_date=None,
                override_range_end_date=None,
                override_note=override.note,
                updated_at=schedule.updated_at,
            )
        range_override = next(
            (
                item
                for item in schedule.date_range_overrides
                if item.start_date <= local_date <= item.end_date
            ),
            None,
        )
        if range_override is not None:
            matched_window = self._match_shift_window(
                range_override.windows,
                minute_of_day=minute_of_day,
            )
            if matched_window is not None:
                return ReplayWorkerMonitorResolvedShiftResponse(
                    workspace_id=workspace_id,
                    timezone=schedule.timezone,
                    evaluated_at=resolved_at,
                    shift_label=matched_window.shift_label,
                    source="date_range_override",
                    matched_window=matched_window,
                    override_date=None,
                    override_range_start_date=range_override.start_date,
                    override_range_end_date=range_override.end_date,
                    override_note=range_override.note,
                    updated_at=schedule.updated_at,
                )
            return ReplayWorkerMonitorResolvedShiftResponse(
                workspace_id=workspace_id,
                timezone=schedule.timezone,
                evaluated_at=resolved_at,
                shift_label=None,
                source="date_range_override",
                matched_window=None,
                override_date=None,
                override_range_start_date=range_override.start_date,
                override_range_end_date=range_override.end_date,
                override_note=range_override.note,
                updated_at=schedule.updated_at,
            )
        matched_window = self._match_shift_window(
            schedule.windows,
            minute_of_day=minute_of_day,
        )
        if matched_window is not None:
            return ReplayWorkerMonitorResolvedShiftResponse(
                workspace_id=workspace_id,
                timezone=schedule.timezone,
                evaluated_at=resolved_at,
                shift_label=matched_window.shift_label,
                source="schedule",
                matched_window=matched_window,
                override_date=None,
                override_range_start_date=None,
                override_range_end_date=None,
                override_note=None,
                updated_at=schedule.updated_at,
            )
        return ReplayWorkerMonitorResolvedShiftResponse(
            workspace_id=workspace_id,
            timezone=schedule.timezone,
            evaluated_at=resolved_at,
            shift_label=None,
            source="none",
            matched_window=None,
            override_date=None,
            override_range_start_date=None,
            override_range_end_date=None,
            override_note=None,
            updated_at=schedule.updated_at,
        )

    def delete_replay_worker_monitor_preset(
        self,
        workspace_id: str,
        preset_name: str,
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerMonitorPresetDeleteResponse:
        normalized_preset_name = self._normalize_replay_worker_monitor_preset_name(preset_name)
        deleted = self.repository.delete_replay_worker_monitor_preset(
            workspace_id,
            normalized_preset_name,
        )
        if deleted is None:
            raise KeyError(normalized_preset_name)
        response = ReplayWorkerMonitorPresetDeleteResponse(
            workspace_id=workspace_id,
            preset_name=normalized_preset_name,
            deleted=True,
        )
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.delete_worker_monitor_preset",
            subject_type="replay_worker_monitor_preset",
            subject_id=f"{workspace_id}:{normalized_preset_name}",
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload={
                "preset_name": normalized_preset_name,
            },
            result_payload={
                **response.model_dump(mode="json"),
                "deleted_preset": deleted.model_dump(mode="json"),
            },
        )
        return response

    def update_replay_worker_alert_policy(
        self,
        workspace_id: str,
        command: ReplayWorkerAlertPolicyUpdateCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayWorkerAlertPolicySummary:
        payload = self._coerce_command(command, ReplayWorkerAlertPolicyUpdateCommand)
        (
            warning_consecutive_failures,
            critical_consecutive_failures,
        ) = self._validate_replay_worker_alert_thresholds(
            warning_consecutive_failures=payload.warning_consecutive_failures,
            critical_consecutive_failures=payload.critical_consecutive_failures,
        )
        default_policy = self._default_replay_worker_alert_policy(workspace_id)
        if (
            warning_consecutive_failures == default_policy.warning_consecutive_failures
            and critical_consecutive_failures == default_policy.critical_consecutive_failures
        ):
            self.repository.delete_replay_worker_alert_policy(workspace_id)
            response = default_policy
        else:
            self.repository.upsert_replay_worker_alert_policy(
                workspace_id=workspace_id,
                warning_consecutive_failures=warning_consecutive_failures,
                critical_consecutive_failures=critical_consecutive_failures,
            )
            response = self._resolve_replay_worker_alert_policy(workspace_id)
        self.repository.record_replay_admin_audit_log(
            workspace_id=workspace_id,
            action_type="replay.update_worker_alert_policy",
            subject_type="replay_worker_alert_policy",
            subject_id=workspace_id,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload=payload.model_dump(mode="json"),
            result_payload=response.model_dump(mode="json"),
        )
        return response

    @staticmethod
    def _latest_replay_worker_failure(history):
        return next(
            (
                item
                for item in history
                if item.status in {"retrying", "failed", "degraded"} or item.error_message is not None
            ),
            None,
        )

    def _default_replay_worker_alert_policy(
        self,
        workspace_id: str | None = None,
    ) -> ReplayWorkerAlertPolicySummary:
        return ReplayWorkerAlertPolicySummary(
            workspace_id=workspace_id,
            warning_consecutive_failures=self.replay_worker_alert_warning_consecutive_failures,
            critical_consecutive_failures=self.replay_worker_alert_critical_consecutive_failures,
            default_warning_consecutive_failures=self.replay_worker_alert_warning_consecutive_failures,
            default_critical_consecutive_failures=self.replay_worker_alert_critical_consecutive_failures,
            source="default",
            updated_at=None,
        )

    def _resolve_replay_worker_alert_policy(
        self,
        workspace_id: str | None,
    ) -> ReplayWorkerAlertPolicySummary:
        default_policy = self._default_replay_worker_alert_policy(workspace_id)
        if workspace_id is None:
            return default_policy
        override = self.repository.get_replay_worker_alert_policy(workspace_id)
        if override is not None:
            return ReplayWorkerAlertPolicySummary(
                workspace_id=override.workspace_id,
                warning_consecutive_failures=override.warning_consecutive_failures,
                critical_consecutive_failures=override.critical_consecutive_failures,
                default_warning_consecutive_failures=default_policy.warning_consecutive_failures,
                default_critical_consecutive_failures=default_policy.critical_consecutive_failures,
                source=override.source,
                updated_at=override.updated_at,
            )
        return default_policy

    def _build_replay_worker_alert(
        self,
        *,
        current,
        latest_failure,
        policy: ReplayWorkerAlertPolicySummary,
    ) -> ReplayWorkerAlertSummary | None:
        if current is None and latest_failure is None:
            return ReplayWorkerAlertSummary(
                level="warning",
                headline="No worker heartbeat observed",
                detail="No replay worker heartbeat has been persisted for this workspace yet.",
            )
        if current is not None and current.status == "failed":
            return ReplayWorkerAlertSummary(
                level="critical",
                headline="Replay worker failed",
                detail=(
                    current.error_message
                    or f"Worker stopped after {current.consecutive_failures} consecutive failures."
                ),
                latest_failure_status=current.status,
                latest_failure_at=current.last_seen_at,
                latest_failure_message=current.error_message,
            )
        if (
            current is not None
            and current.consecutive_failures >= policy.critical_consecutive_failures
        ):
            return ReplayWorkerAlertSummary(
                level="critical",
                headline="Replay worker failure threshold breached",
                detail=(
                    current.error_message
                    or (
                        f"Worker has {current.consecutive_failures} consecutive failures, "
                        f"meeting the critical threshold of "
                        f"{policy.critical_consecutive_failures}."
                    )
                ),
                latest_failure_status=current.status,
                latest_failure_at=current.last_seen_at,
                latest_failure_message=current.error_message,
            )
        if current is not None and (
            current.status in {"retrying", "degraded"}
            or current.consecutive_failures >= policy.warning_consecutive_failures
        ):
            return ReplayWorkerAlertSummary(
                level="warning",
                headline="Replay worker retrying",
                detail=(
                    current.error_message
                    or (
                        f"Worker has {current.consecutive_failures} consecutive failures; "
                        f"critical threshold is "
                        f"{policy.critical_consecutive_failures}."
                    )
                ),
                latest_failure_status=current.status,
                latest_failure_at=current.last_seen_at,
                latest_failure_message=current.error_message,
            )
        if latest_failure is not None:
            return ReplayWorkerAlertSummary(
                level="warning",
                headline="Recent worker failure detected",
                detail=(
                    latest_failure.error_message
                    or f"Latest failure status was {latest_failure.status} before the worker recovered."
                ),
                latest_failure_status=latest_failure.status,
                latest_failure_at=latest_failure.emitted_at,
                latest_failure_message=latest_failure.error_message,
            )
        assert current is not None
        return ReplayWorkerAlertSummary(
            level="healthy",
            headline="Replay worker healthy",
            detail=(
                f"Last heartbeat is {current.status} with "
                f"{current.remaining_queued_count} queued replay runs remaining."
            ),
        )

    def list_incidents(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        severity: str | None = None,
        service_id: str | None = None,
    ) -> list[IncidentSummary]:
        return self.repository.list_incidents(
            workspace_id,
            status=status,
            severity=severity,
            service_id=service_id,
        )

    def get_incident_workspace(self, incident_id: str) -> IncidentWorkspaceResponse:
        return self.repository.get_incident_workspace(incident_id)

    def list_hypotheses(self, incident_id: str) -> list[HypothesisSummary]:
        return self.repository.list_hypotheses(incident_id)

    def list_recommendations(self, incident_id: str) -> list[RecommendationSummary]:
        return self.repository.list_recommendations(incident_id)

    def list_comms(
        self,
        incident_id: str,
        *,
        channel: str | None = None,
        status: str | None = None,
    ) -> list[CommsDraftSummary]:
        return self.repository.list_comms(incident_id, channel=channel, status=status)

    def list_audit_logs(
        self,
        incident_id: str,
        *,
        action_type: str | None = None,
        actor_user_id: str | None = None,
    ):
        return self.repository.list_audit_logs(
            incident_id,
            action_type=action_type,
            actor_user_id=actor_user_id,
        )

    def list_replay_admin_audit_logs(
        self,
        workspace_id: str,
        *,
        action_type: str | None = None,
        actor_user_id: str | None = None,
        request_id: str | None = None,
    ) -> list[ReplayAdminAuditLogSummary]:
        return self.repository.list_replay_admin_audit_logs(
            workspace_id,
            action_type=action_type,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def list_approval_tasks(self, incident_id: str) -> list[ApprovalTaskSummary]:
        return self.repository.list_approval_tasks(incident_id)

    def get_approval_task(self, approval_task_id: str) -> ApprovalTaskSummary:
        return self.repository.get_approval_task(approval_task_id)

    def decide_approval_task(
        self,
        approval_task_id: str,
        command: ApprovalDecisionCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> ApprovalDecisionResponse:
        if isinstance(command, dict):
            command = ApprovalDecisionCommand.model_validate(command)
        if command.decision != "approve" and (
            command.execute_recommendation
            or command.publish_linked_drafts
            or bool(command.linked_draft_ids)
        ):
            raise ValueError("APPROVAL_DECISION_INVALID")
        if (command.publish_linked_drafts or command.linked_draft_ids) and command.expected_fact_set_version is None:
            raise ValueError("APPROVAL_PUBLISH_FACT_SET_REQUIRED")
        request_payload = {"approval_task_id": approval_task_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.decide_approval_task",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=ApprovalDecisionResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.decide_approval_task(
            approval_task_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        approval_task = response.approval_task
        self._emit_incident_event(
            incident_id=approval_task.incident_id,
            event_name="opsgraph.approval.updated",
            aggregate_type="approval_task",
            aggregate_id=approval_task.approval_task_id,
            node_name="approval_task_decided",
            payload={
                "approval_task_id": approval_task.approval_task_id,
                "status": approval_task.status,
                "recommendation_id": approval_task.recommendation_id,
                "decision": command.decision,
                "published_draft_ids": [item.draft_id for item in response.published_drafts],
            },
        )
        if response.recommendation is not None:
            self._emit_incident_event(
                incident_id=approval_task.incident_id,
                event_name="opsgraph.incident.updated",
                aggregate_type="runbook_recommendation",
                aggregate_id=response.recommendation.recommendation_id,
                node_name="recommendation_orchestrated",
                payload={
                    "recommendation_id": response.recommendation.recommendation_id,
                    "recommendation_status": response.recommendation.status,
                    "approval_task_id": response.recommendation.approval_task_id,
                },
            )
        for published_draft in response.published_drafts:
            self._emit_incident_event(
                incident_id=approval_task.incident_id,
                event_name="opsgraph.comms.updated",
                aggregate_type="comms_draft",
                aggregate_id=published_draft.draft_id,
                node_name="comms_published_from_approval",
                payload={
                    "draft_id": published_draft.draft_id,
                    "comms_status": published_draft.status,
                    "published_message_ref": published_draft.published_message_ref,
                    "approval_task_id": approval_task.approval_task_id,
                },
            )
        self._store_idempotent_response(
            operation="opsgraph.decide_approval_task",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def add_fact(
        self,
        incident_id: str,
        command: FactCreateCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> FactMutationResponse:
        if isinstance(command, dict):
            command = FactCreateCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.add_fact",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=FactMutationResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.add_fact(
            incident_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident",
            aggregate_id=incident_id,
            node_name="fact_added",
            payload={
                "fact_id": response.fact_id,
                "mutation": "fact_added",
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.add_fact",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def retract_fact(
        self,
        incident_id: str,
        fact_id: str,
        command: FactRetractCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> FactMutationResponse:
        if isinstance(command, dict):
            command = FactRetractCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, "fact_id": fact_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.retract_fact",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=FactMutationResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.retract_fact(
            incident_id,
            fact_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident_fact",
            aggregate_id=fact_id,
            node_name="fact_retracted",
            payload={
                "fact_id": fact_id,
                "mutation": "fact_retracted",
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.retract_fact",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def override_severity(
        self,
        incident_id: str,
        command: SeverityOverrideCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = SeverityOverrideCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.override_severity",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=IncidentSummary,
        )
        if cached is not None:
            return cached
        response = self.repository.override_severity(
            incident_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident",
            aggregate_id=incident_id,
            node_name="severity_overridden",
            payload={
                "mutation": "severity_overridden",
                "reason": command.reason,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.override_severity",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def decide_hypothesis(
        self,
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> HypothesisDecisionResponse:
        if isinstance(command, dict):
            command = HypothesisDecisionCommand.model_validate(command)
        request_payload = {
            "incident_id": incident_id,
            "hypothesis_id": hypothesis_id,
            **command.model_dump(mode="json"),
        }
        cached = self._load_idempotent_response(
            operation="opsgraph.decide_hypothesis",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=HypothesisDecisionResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.decide_hypothesis(
            incident_id,
            hypothesis_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.hypothesis.updated",
            aggregate_type="hypothesis",
            aggregate_id=hypothesis_id,
            node_name="hypothesis_decided",
            payload={
                "hypothesis_id": hypothesis_id,
                "hypothesis_status": response.status,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.decide_hypothesis",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def decide_recommendation(
        self,
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> RecommendationDecisionResponse:
        if isinstance(command, dict):
            command = RecommendationDecisionCommand.model_validate(command)
        request_payload = {
            "incident_id": incident_id,
            "recommendation_id": recommendation_id,
            **command.model_dump(mode="json"),
        }
        cached = self._load_idempotent_response(
            operation="opsgraph.decide_recommendation",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=RecommendationDecisionResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.decide_recommendation(
            incident_id,
            recommendation_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="runbook_recommendation",
            aggregate_id=recommendation_id,
            node_name="recommendation_decided",
            payload={
                "recommendation_id": recommendation_id,
                "recommendation_status": response.status,
                "approval_task_id": response.approval_task_id,
            },
        )
        if response.approval_task_id is not None and response.approval_status is not None and command.decision in {
            "approve",
            "reject",
        }:
            self._emit_incident_event(
                incident_id=incident_id,
                event_name="opsgraph.approval.updated",
                aggregate_type="approval_task",
                aggregate_id=response.approval_task_id,
                node_name="approval_task_updated",
                payload={
                    "approval_task_id": response.approval_task_id,
                    "recommendation_id": recommendation_id,
                    "status": response.approval_status,
                    "decision": command.decision,
                },
            )
        self._store_idempotent_response(
            operation="opsgraph.decide_recommendation",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def publish_comms(
        self,
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> CommsPublishResponse:
        if isinstance(command, dict):
            command = CommsPublishCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, "draft_id": draft_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.publish_comms",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=CommsPublishResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.publish_comms(
            incident_id,
            draft_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.comms.updated",
            aggregate_type="comms_draft",
            aggregate_id=draft_id,
            node_name="comms_published",
            payload={
                "draft_id": draft_id,
                "comms_status": response.status,
                "published_message_ref": response.published_message_ref,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.publish_comms",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def resolve_incident(
        self,
        incident_id: str,
        command: ResolveIncidentCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = ResolveIncidentCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.resolve_incident",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=IncidentSummary,
        )
        if cached is not None:
            return cached
        response = self.repository.resolve_incident(
            incident_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident",
            aggregate_id=incident_id,
            node_name="incident_resolved",
            payload={
                "mutation": "incident_resolved",
                "resolution_summary": command.resolution_summary,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.resolve_incident",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def close_incident(
        self,
        incident_id: str,
        command: CloseIncidentCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = CloseIncidentCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.close_incident",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=IncidentSummary,
        )
        if cached is not None:
            return cached
        response = self.repository.close_incident(
            incident_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident",
            aggregate_id=incident_id,
            node_name="incident_closed",
            payload={
                "mutation": "incident_closed",
                "close_reason": command.close_reason,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.close_incident",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def get_postmortem(self, incident_id: str) -> PostmortemSummary:
        return self.repository.get_postmortem(incident_id)

    def finalize_postmortem(
        self,
        incident_id: str,
        command: PostmortemFinalizeCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> PostmortemSummary:
        if isinstance(command, dict):
            command = PostmortemFinalizeCommand.model_validate(command)
        request_payload = {"incident_id": incident_id, **command.model_dump(mode="json")}
        cached = self._load_idempotent_response(
            operation="opsgraph.finalize_postmortem",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=PostmortemSummary,
        )
        if cached is not None:
            return cached
        response = self.repository.finalize_postmortem(
            incident_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.postmortem.updated",
            aggregate_type="postmortem",
            aggregate_id=response.postmortem_id,
            node_name="postmortem_finalized",
            payload={
                "postmortem_id": response.postmortem_id,
                "postmortem_status": response.status,
                "finalized_by_user_id": response.finalized_by_user_id,
                "finalized_at": (
                    response.finalized_at.isoformat().replace("+00:00", "Z")
                    if response.finalized_at is not None
                    else None
                ),
                "replay_case_id": response.replay_case_id,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.finalize_postmortem",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def list_postmortems(
        self,
        workspace_id: str,
        *,
        incident_id: str | None = None,
        status: str | None = None,
    ) -> list[PostmortemSummary]:
        return self.repository.list_postmortems(
            workspace_id,
            incident_id=incident_id,
            status=status,
        )

    def start_replay_run(
        self,
        command: ReplayRunCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayRunSummary:
        if isinstance(command, dict):
            command = ReplayRunCommand.model_validate(command)
        request_payload = command.model_dump(mode="json")
        cached = self._load_idempotent_response(
            operation="opsgraph.start_replay_run",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=ReplayRunSummary,
        )
        if cached is not None:
            return cached
        response = self.repository.start_replay_run(
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            idempotency_key=idempotency_key,
        )
        self._store_idempotent_response(
            operation="opsgraph.start_replay_run",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def list_replays(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
        status: str | None = None,
    ) -> list[ReplayRunSummary]:
        return self.repository.list_replays(
            workspace_id,
            incident_id,
            replay_case_id,
            status,
        )

    def list_replay_cases(
        self,
        workspace_id: str,
        incident_id: str | None = None,
    ) -> list[ReplayCaseSummary]:
        return self.repository.list_replay_cases(workspace_id, incident_id)

    def get_replay_case(self, replay_case_id: str) -> ReplayCaseDetail:
        return self.repository.get_replay_case(replay_case_id)

    def list_replay_baselines(
        self,
        workspace_id: str,
        incident_id: str | None = None,
    ) -> list[ReplayBaselineSummary]:
        return self.repository.list_replay_baselines(workspace_id, incident_id)

    def list_replay_evaluations(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_run_id: str | None = None,
        replay_case_id: str | None = None,
    ) -> list[ReplayEvaluationSummary]:
        return self.repository.list_replay_evaluations(
            workspace_id,
            incident_id,
            replay_run_id,
            replay_case_id,
        )

    def update_replay_status(
        self,
        replay_run_id: str,
        command: ReplayStatusCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayRunSummary:
        if isinstance(command, dict):
            command = ReplayStatusCommand.model_validate(command)
        return self.repository.update_replay_status(
            replay_run_id,
            command,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
        )

    def execute_replay_run(
        self,
        replay_run_id: str,
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayRunSummary:
        audit_context = self._build_audit_context(auth_context, request_id=request_id)
        replay = self.repository.mark_replay_execution(
            replay_run_id,
            status="running",
            actor_context=audit_context,
        )
        try:
            if self.shared_platform is None or self.workflow_registry is None or self.prompt_service is None:
                raise ValueError("Replay execution requires shared platform runtime components")
            if replay.replay_case_id is not None:
                seed = self.repository.get_replay_case_input_snapshot(replay.replay_case_id)
            else:
                seed = self.repository.get_incident_execution_seed(replay.incident_id)
            workflow_run_id = f"{replay_run_id}-replay"
            fixture_store = seed_incident_response_replay_fixtures(
                workflow_run_id=workflow_run_id,
                fixture_store=self.replay_fixture_store,
            )
            replay_loader = self.shared_platform.ReplayFixtureLoader(fixture_store)
            replay_execution_service = self.shared_platform.WorkflowExecutionService(
                self.prompt_service,
                replay_loader=replay_loader,
                state_store=self.runtime_stores.state_store if self.runtime_stores is not None else None,
                checkpoint_store=self.runtime_stores.checkpoint_store if self.runtime_stores is not None else None,
                replay_store=self.runtime_stores.replay_store if self.runtime_stores is not None else None,
            )
            replay_api_service = self.shared_platform.WorkflowApiService(
                self.workflow_registry,
                replay_execution_service,
                runtime_stores=self.runtime_stores,
            )
            result = replay_api_service.replay_workflow(
                {
                    "workflow_name": "opsgraph_incident_response",
                    "workflow_run_id": workflow_run_id,
                    "input_payload": seed,
                    "state_overrides": {"trigger_type": "system_replay"},
                }
            )
        except Exception as exc:
            return self.repository.mark_replay_execution(
                replay_run_id,
                status="failed",
                error_message=str(exc),
                actor_context=audit_context,
                audit_action_type="replay.execute",
                request_payload={"action": "execute"},
            )
        return self.repository.mark_replay_execution(
            replay_run_id,
            status="completed",
            workflow_run_id=result.workflow_run_id,
            current_state=result.current_state,
            error_message=None,
            actor_context=audit_context,
            audit_action_type="replay.execute",
            request_payload={"action": "execute"},
        )

    def process_queued_replays(
        self,
        workspace_id: str,
        *,
        limit: int = 20,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayQueueProcessResponse:
        if limit < 1:
            raise ValueError("INVALID_REPLAY_BATCH_LIMIT")
        queued = sorted(
            self.list_replays(workspace_id, status="queued"),
            key=lambda item: item.created_at,
        )
        selected = queued[:limit]
        items: list[ReplayRunSummary] = []
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        for replay in selected:
            try:
                executed = self.execute_replay_run(
                    replay.replay_run_id,
                    auth_context=auth_context,
                    request_id=request_id,
                )
            except ValueError as exc:
                if str(exc) != "REPLAY_STATUS_CONFLICT":
                    raise
                skipped_count += 1
                items.append(self.repository.get_replay_run(replay.replay_run_id))
                continue
            items.append(executed)
            if executed.status == "completed":
                completed_count += 1
            elif executed.status == "failed":
                failed_count += 1
            else:
                skipped_count += 1
        remaining_queued_count = len(self.list_replays(workspace_id, status="queued"))
        return ReplayQueueProcessResponse(
            workspace_id=workspace_id,
            queued_count=len(queued),
            processed_count=len(selected),
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            remaining_queued_count=remaining_queued_count,
            items=items,
        )

    def capture_replay_baseline(
        self,
        command: ReplayBaselineCaptureCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayBaselineSummary:
        if isinstance(command, dict):
            command = ReplayBaselineCaptureCommand.model_validate(command)
        if self.runtime_stores is None or self.workflow_api_service is None:
            raise ValueError("Baseline capture requires runtime stores")
        seed = self.repository.get_incident_execution_seed(command.incident_id)
        workflow_run_id = command.workflow_run_id or f"baseline-{command.incident_id}-{command.model_bundle_version}"
        result = self.workflow_api_service.start_workflow(
            {
                "workflow_name": "opsgraph_incident_response",
                "workflow_run_id": workflow_run_id,
                "input_payload": seed,
                "state_overrides": {"trigger_type": "api_command", "baseline_capture": True},
            }
        )
        replay_records = self.runtime_stores.replay_store.list_for_run(workflow_run_id)
        node_summaries = [
            ReplayNodeSummary(
                checkpoint_seq=record.checkpoint_seq,
                bundle_id=record.bundle_id,
                bundle_version=record.bundle_version,
                output_summary=record.output_summary,
                recorded_at=record.recorded_at,
            )
            for record in replay_records
        ]
        return self.repository.record_replay_baseline(
            incident_id=command.incident_id,
            workflow_run_id=workflow_run_id,
            model_bundle_version=command.model_bundle_version,
            workflow_type=result.workflow_type,
            final_state=result.current_state,
            checkpoint_seq=result.checkpoint_seq,
            node_summaries=node_summaries,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
        )

    def evaluate_replay_run(
        self,
        replay_run_id: str,
        command: ReplayEvaluationCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> ReplayEvaluationSummary:
        if isinstance(command, dict):
            command = ReplayEvaluationCommand.model_validate(command)
        if self.runtime_stores is None:
            raise ValueError("REPLAY_EVALUATION_UNAVAILABLE")
        replay = self.repository.get_replay_run(replay_run_id)
        baseline = self.repository.get_replay_baseline(command.baseline_id)
        if replay.workflow_run_id is None:
            raise ValueError("REPLAY_RUN_NOT_EXECUTED")
        replay_state = self.get_workflow_state(replay.workflow_run_id)
        replay_records = self.runtime_stores.replay_store.list_for_run(replay.workflow_run_id)
        replay_nodes = [
            ReplayNodeSummary(
                checkpoint_seq=record.checkpoint_seq,
                bundle_id=record.bundle_id,
                bundle_version=record.bundle_version,
                output_summary=record.output_summary,
                recorded_at=record.recorded_at,
            )
            for record in replay_records
        ]
        mismatches: list[str] = []
        node_diffs: list[ReplayNodeDiffSummary] = []
        if replay_state.current_state != baseline.final_state:
            mismatches.append(
                f"final_state mismatch: expected {baseline.final_state}, got {replay_state.current_state}"
            )
        if replay_state.checkpoint_seq != baseline.checkpoint_seq:
            mismatches.append(
                f"checkpoint_seq mismatch: expected {baseline.checkpoint_seq}, got {replay_state.checkpoint_seq}"
            )
        if len(replay_nodes) != len(baseline.node_summaries):
            mismatches.append(
                f"node_count mismatch: expected {len(baseline.node_summaries)}, got {len(replay_nodes)}"
            )
        baseline_origin = baseline.node_summaries[0].recorded_at if baseline.node_summaries else None
        replay_origin = replay_nodes[0].recorded_at if replay_nodes else None
        max_nodes = max(len(baseline.node_summaries), len(replay_nodes))
        for index in range(max_nodes):
            baseline_node = baseline.node_summaries[index] if index < len(baseline.node_summaries) else None
            replay_node = replay_nodes[index] if index < len(replay_nodes) else None
            node_mismatches: list[str] = []
            if baseline_node is None:
                node_mismatches.append("missing baseline node")
            if replay_node is None:
                node_mismatches.append("missing replay node")
            if baseline_node is not None and replay_node is not None:
                if baseline_node.bundle_id != replay_node.bundle_id:
                    node_mismatches.append(
                        f"bundle mismatch: expected {baseline_node.bundle_id}, got {replay_node.bundle_id}"
                    )
                if baseline_node.bundle_version != replay_node.bundle_version:
                    node_mismatches.append(
                        f"version mismatch: expected {baseline_node.bundle_version}, got {replay_node.bundle_version}"
                    )
                if baseline_node.output_summary != replay_node.output_summary:
                    node_mismatches.append(
                        f"summary mismatch: expected '{baseline_node.output_summary}', got '{replay_node.output_summary}'"
                    )
            mismatches.extend(f"node[{index}] {item}" for item in node_mismatches)
            baseline_elapsed_ms = (
                int((baseline_node.recorded_at - baseline_origin).total_seconds() * 1000)
                if baseline_node is not None and baseline_node.recorded_at is not None and baseline_origin is not None
                else None
            )
            replay_elapsed_ms = (
                int((replay_node.recorded_at - replay_origin).total_seconds() * 1000)
                if replay_node is not None and replay_node.recorded_at is not None and replay_origin is not None
                else None
            )
            latency_delta_ms = (
                replay_elapsed_ms - baseline_elapsed_ms
                if baseline_elapsed_ms is not None and replay_elapsed_ms is not None
                else None
            )
            node_diffs.append(
                ReplayNodeDiffSummary(
                    checkpoint_seq=(
                        baseline_node.checkpoint_seq
                        if baseline_node is not None
                        else (replay_node.checkpoint_seq if replay_node is not None else index + 1)
                    ),
                    matched=not node_mismatches,
                    expected_bundle_id=(baseline_node.bundle_id if baseline_node is not None else "missing"),
                    actual_bundle_id=(replay_node.bundle_id if replay_node is not None else None),
                    expected_bundle_version=(
                        baseline_node.bundle_version if baseline_node is not None else "missing"
                    ),
                    actual_bundle_version=(replay_node.bundle_version if replay_node is not None else None),
                    expected_output_summary=(
                        baseline_node.output_summary if baseline_node is not None else "missing"
                    ),
                    actual_output_summary=(replay_node.output_summary if replay_node is not None else None),
                    baseline_elapsed_ms=baseline_elapsed_ms,
                    replay_elapsed_ms=replay_elapsed_ms,
                    latency_delta_ms=latency_delta_ms,
                    mismatch_reasons=node_mismatches,
                )
            )
        status = "matched" if not mismatches else "mismatched"
        max_checks = max(1, 2 + max(len(baseline.node_summaries), len(replay_nodes)) * 3)
        score = max(0.0, 1.0 - (len(mismatches) / max_checks))
        evaluation = self.repository.record_replay_evaluation(
            baseline_id=baseline.baseline_id,
            replay_run_id=replay_run_id,
            incident_id=baseline.incident_id,
            status=status,
            score=score,
            mismatches=mismatches,
            baseline_final_state=baseline.final_state,
            replay_final_state=replay_state.current_state,
            baseline_checkpoint_seq=baseline.checkpoint_seq,
            replay_checkpoint_seq=replay_state.checkpoint_seq,
            node_diffs=node_diffs,
        )
        artifact_path = write_replay_report_artifacts(
            report_id=evaluation.report_id,
            payload={
                "baseline": baseline.model_dump(mode="json"),
                "replay": replay.model_dump(mode="json"),
                "report": evaluation.model_dump(mode="json"),
            },
        )
        return self.repository.attach_replay_evaluation_artifact(
            evaluation.report_id,
            report_artifact_path=artifact_path,
            actor_context=self._build_audit_context(auth_context, request_id=request_id),
            request_payload=command.model_dump(mode="json"),
        )

    def ingest_alert(
        self,
        command: AlertIngestCommand | dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AlertIngestResponse:
        if isinstance(command, dict):
            command = AlertIngestCommand.model_validate(command)
        request_payload = command.model_dump(mode="json")
        cached = self._load_idempotent_response(
            operation="opsgraph.ingest_alert",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            model_type=AlertIngestResponse,
        )
        if cached is not None:
            return cached
        response = self.repository.ingest_alert(
            ops_workspace_id=command.ops_workspace_id,
            correlation_key=command.correlation_key,
            summary=command.summary,
            observed_at=command.observed_at,
            source=command.source,
        )
        incident = self.repository.get_incident_workspace(response.incident_id).incident
        self._emit_product_event(
            event_name="opsgraph.signal.ingested",
            workflow_run_id=response.workflow_run_id or f"opsgraph-alert-{response.signal_id}",
            aggregate_type="incident",
            aggregate_id=response.incident_id,
            node_name="signal_ingested",
            payload={
                "signal_id": response.signal_id,
                "source": command.source,
                "dedupe_key": command.correlation_key,
                "incident_id": response.incident_id,
                "organization_id": command.organization_id,
                "workspace_id": command.workspace_id,
            },
        )
        self._emit_product_event(
            event_name="opsgraph.incident.created" if response.incident_created else "opsgraph.incident.updated",
            workflow_run_id=response.workflow_run_id or f"opsgraph-alert-{response.signal_id}",
            aggregate_type="incident",
            aggregate_id=response.incident_id,
            node_name="incident_correlated",
            payload={
                "incident_id": response.incident_id,
                "incident_key": incident.incident_key,
                "severity": incident.severity,
                "status": incident.incident_status,
                "organization_id": command.organization_id,
                "workspace_id": command.workspace_id,
            },
        )
        self._store_idempotent_response(
            operation="opsgraph.ingest_alert",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def respond_to_incident(self, command: IncidentResponseCommand | dict[str, Any]) -> OpsGraphRunResponse:
        command = self._coerce_command(command, IncidentResponseCommand)
        response, run_result = self._run_registered_workflow(
            workflow_name="opsgraph_incident_response",
            workflow_run_id=command.workflow_run_id,
            input_payload={
                "incident_id": command.incident_id,
                "ops_workspace_id": command.ops_workspace_id,
                "signal_ids": command.signal_ids,
                "signal_summaries": command.signal_summaries,
                "current_incident_candidates": command.current_incident_candidates,
                "context_bundle_id": command.context_bundle_id,
                "current_fact_set_version": command.current_fact_set_version,
                "service_id": command.service_id,
                "confirmed_fact_refs": command.confirmed_fact_refs,
                "top_hypothesis_refs": command.top_hypothesis_refs,
                "target_channels": command.target_channels,
                "organization_id": command.organization_id,
                "workspace_id": command.workspace_id,
            },
            state_overrides=command.state_overrides,
        )
        generation_result = self.repository.record_incident_response_result(
            incident_id=command.incident_id,
            workflow_run_id=response.workflow_run_id,
            checkpoint_seq=response.checkpoint_seq,
            triage_output=self._step_structured_output(run_result, "triage"),
            investigation_output=self._step_structured_output(run_result, "hypothesize"),
            recommendation_output=self._step_structured_output(run_result, "advise"),
            comms_output=self._step_structured_output(run_result, "communicate"),
        )
        self._sync_incident_workflow_state(
            workflow_run_id=response.workflow_run_id,
            workflow_type=response.workflow_type,
            incident_id=command.incident_id,
        )
        approval_task_id = generation_result.get("approval_task_id")
        recommendation_id = generation_result.get("recommendation_id")
        if approval_task_id is not None and recommendation_id is not None:
            self._emit_incident_event(
                incident_id=command.incident_id,
                event_name="opsgraph.approval.requested",
                aggregate_type="approval_task",
                aggregate_id=str(approval_task_id),
                node_name="approval_requested",
                workflow_run_id=response.workflow_run_id,
                payload={
                    "approval_task_id": str(approval_task_id),
                    "subject_type": "runbook_recommendation",
                    "subject_id": str(recommendation_id),
                },
            )
        self._emit_incident_event(
            incident_id=command.incident_id,
            event_name="opsgraph.incident.updated",
            aggregate_type="incident",
            aggregate_id=command.incident_id,
            node_name="incident_response_completed",
            workflow_run_id=response.workflow_run_id,
            payload={
                "current_state": response.current_state,
                "workflow_type": response.workflow_type,
            },
        )
        return response

    def build_retrospective(self, command: RetrospectiveCommand | dict[str, Any]) -> OpsGraphRunResponse:
        command = self._coerce_command(command, RetrospectiveCommand)
        response, run_result = self._run_registered_workflow(
            workflow_name="opsgraph_retrospective",
            workflow_run_id=command.workflow_run_id,
            input_payload={
                "incident_id": command.incident_id,
                "ops_workspace_id": command.ops_workspace_id,
                "current_fact_set_version": command.current_fact_set_version,
                "confirmed_fact_refs": command.confirmed_fact_refs,
                "timeline_refs": command.timeline_refs,
                "resolution_summary": command.resolution_summary,
                "organization_id": command.organization_id,
                "workspace_id": command.workspace_id,
            },
            state_overrides=command.state_overrides,
        )
        self.repository.record_retrospective_result(
            incident_id=command.incident_id,
            workflow_run_id=response.workflow_run_id,
            checkpoint_seq=response.checkpoint_seq,
            postmortem_output=self._step_structured_output(run_result, "retrospective"),
        )
        _, postmortem = self._sync_retrospective_workflow_state(
            workflow_run_id=response.workflow_run_id,
            workflow_type=response.workflow_type,
            incident_id=command.incident_id,
        )
        self._emit_incident_event(
            incident_id=command.incident_id,
            event_name="opsgraph.postmortem.ready",
            aggregate_type="postmortem",
            aggregate_id=postmortem.postmortem_id,
            node_name="retrospective_completed",
            workflow_run_id=response.workflow_run_id,
            payload={
                "postmortem_id": postmortem.postmortem_id,
                "fact_set_version": postmortem.fact_set_version,
                "postmortem_status": postmortem.status,
                "artifact_id": postmortem.artifact_id,
            },
        )
        return response

    def get_workflow_state(self, workflow_run_id: str) -> OpsGraphWorkflowStateResponse:
        state = self.workflow_api_service.execution_service.load_workflow_state(workflow_run_id)
        return OpsGraphWorkflowStateResponse(
            workflow_run_id=workflow_run_id,
            workflow_type=str(state.get("workflow_type", "opsgraph_incident")),
            current_state=str(state.get("current_state", "")),
            checkpoint_seq=int(state.get("checkpoint_seq", 0)),
            raw_state=state,
        )

    def close(self) -> None:
        if self.runtime_stores is not None and hasattr(self.runtime_stores, "dispose"):
            self.runtime_stores.dispose()

    def _emit_product_event(
        self,
        *,
        event_name: str,
        workflow_run_id: str,
        aggregate_type: str,
        aggregate_id: str,
        node_name: str,
        payload: dict[str, object],
    ) -> None:
        if self.runtime_stores is None or not hasattr(self.runtime_stores, "outbox_store"):
            return
        shared_platform = self.shared_platform or load_shared_agent_platform()
        self.runtime_stores.outbox_store.append(
            shared_platform.OutboxEvent(
                event_id=f"product-event-{uuid4().hex[:10]}",
                event_name=event_name,
                workflow_run_id=workflow_run_id,
                workflow_type="opsgraph_alert_ingest",
                node_name=node_name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                emitted_at=datetime.now(UTC),
            )
        )

    def _emit_incident_event(
        self,
        *,
        incident_id: str,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        node_name: str,
        workflow_run_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        context = self.repository.get_incident_event_context(incident_id)
        merged_payload = {
            "incident_id": incident_id,
            "workspace_id": context["workspace_id"],
            "incident_key": context["incident_key"],
            "severity": context["severity"],
            "status": context["status"],
            "current_fact_set_version": context["current_fact_set_version"],
            **(payload or {}),
        }
        self._emit_product_event(
            event_name=event_name,
            workflow_run_id=workflow_run_id or str(context.get("latest_workflow_run_id") or f"opsgraph-{incident_id}"),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            node_name=node_name,
            payload=merged_payload,
        )
