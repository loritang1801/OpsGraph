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
    ReplayQualitySummary,
    ReplaySemanticCheckSummary,
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
    RuntimeProviderAlertSummary,
    RemoteProviderSmokeAlertSummary,
    RemoteProviderSmokeAlertItem,
    RemoteProviderSmokeHistorySummary,
    RemoteProviderSmokeProviderSummary,
    RemoteProviderSmokeRunRecord,
    RemoteProviderSmokeCommand,
    RemoteProviderSmokeResponse,
    RuntimeCapabilitiesResponse,
    RecommendationSummary,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    SeverityOverrideCommand,
)
from .repository import OpsGraphRepository
from .replay_fixtures import seed_incident_response_replay_fixtures
from .replay_reports import write_replay_report_artifacts
from .service_runtime_admin import (
    build_remote_provider_smoke_alert_summary as runtime_admin_build_remote_provider_smoke_alert_summary,
    build_runtime_provider_alert_summary as runtime_admin_build_runtime_provider_alert_summary,
    get_health_status as runtime_admin_get_health_status,
    get_runtime_capabilities as runtime_admin_get_runtime_capabilities,
    list_remote_provider_smoke_runs as runtime_admin_list_remote_provider_smoke_runs,
    run_remote_provider_smoke as runtime_admin_run_remote_provider_smoke,
    runtime_provider_is_remote_active as runtime_admin_runtime_provider_is_remote_active,
    summarize_remote_provider_smoke_runs as runtime_admin_summarize_remote_provider_smoke_runs,
)
from .shared_runtime import load_shared_agent_platform

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
        remote_provider_smoke_alert_warning_consecutive_failures: int = 1,
        remote_provider_smoke_alert_critical_consecutive_failures: int = 3,
    ) -> None:
        (
            self.replay_worker_alert_warning_consecutive_failures,
            self.replay_worker_alert_critical_consecutive_failures,
        ) = self._validate_replay_worker_alert_thresholds(
            warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
            critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        )
        (
            self.remote_provider_smoke_alert_warning_consecutive_failures,
            self.remote_provider_smoke_alert_critical_consecutive_failures,
        ) = self._validate_remote_provider_smoke_alert_thresholds(
            warning_consecutive_failures=remote_provider_smoke_alert_warning_consecutive_failures,
            critical_consecutive_failures=remote_provider_smoke_alert_critical_consecutive_failures,
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
    def _validate_remote_provider_smoke_alert_thresholds(
        *,
        warning_consecutive_failures: int,
        critical_consecutive_failures: int,
    ) -> tuple[int, int]:
        if warning_consecutive_failures < 1:
            raise ValueError("INVALID_REMOTE_PROVIDER_SMOKE_ALERT_WARNING_THRESHOLD")
        if critical_consecutive_failures < warning_consecutive_failures:
            raise ValueError("INVALID_REMOTE_PROVIDER_SMOKE_ALERT_CRITICAL_THRESHOLD")
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

    @staticmethod
    def _safe_match_rate(matched_count: int, expected_count: int) -> float | None:
        if expected_count <= 0:
            return None
        return round(matched_count / expected_count, 4)

    @staticmethod
    def _normalize_semantic_text(value: object) -> str:
        return " ".join(str(value or "").split()).strip().casefold()

    @classmethod
    def _normalize_semantic_list(cls, values: list[str]) -> list[str]:
        return [item for item in (cls._normalize_semantic_text(value) for value in values) if item]

    @staticmethod
    def _truncate_semantic_value(value: str, *, limit: int = 80) -> str:
        normalized = " ".join(value.split()).strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."

    @classmethod
    def _summarize_semantic_values(cls, values: list[str], *, item_limit: int = 3) -> str:
        if not values:
            return "none"
        trimmed = [cls._truncate_semantic_value(value) for value in values[:item_limit]]
        if len(values) > item_limit:
            trimmed.append(f"+{len(values) - item_limit} more")
        return " | ".join(trimmed)

    @staticmethod
    def _state_payloads(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
        payload = state.get(key)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    @classmethod
    def _state_hypothesis_titles(cls, state: dict[str, Any]) -> list[str]:
        payloads = sorted(
            cls._state_payloads(state, "hypothesis_payloads"),
            key=lambda item: (
                int(item.get("rank") or 0),
                str(item.get("title") or ""),
            ),
        )
        return [
            str(item.get("title")).strip()
            for item in payloads
            if str(item.get("title") or "").strip()
        ]

    @classmethod
    def _state_recommendation_titles(cls, state: dict[str, Any]) -> list[str]:
        payloads = cls._state_payloads(state, "recommendation_payloads")
        return [
            str(item.get("title")).strip()
            for item in payloads
            if str(item.get("title") or "").strip()
        ]

    @classmethod
    def _state_comms_fingerprints(cls, state: dict[str, Any]) -> list[str]:
        payloads = cls._state_payloads(state, "comms_payloads")
        fingerprints: list[str] = []
        for item in payloads:
            channel_type = str(item.get("channel_type") or "unknown").strip()
            fact_set_version = int(item.get("fact_set_version") or 0)
            body_markdown = cls._truncate_semantic_value(str(item.get("body_markdown") or ""), limit=60)
            fingerprints.append(f"{channel_type}@v{fact_set_version}: {body_markdown}")
        return fingerprints

    @staticmethod
    def _average_or_none(values: list[float | None]) -> float | None:
        normalized = [value for value in values if value is not None]
        if not normalized:
            return None
        return round(sum(normalized) / len(normalized), 4)

    @classmethod
    def _build_replay_semantic_metrics(
        cls,
        *,
        baseline_state: dict[str, Any],
        replay_state: dict[str, Any],
    ) -> dict[str, Any]:
        checks: list[ReplaySemanticCheckSummary] = []
        mismatch_messages: list[str] = []

        def add_check(
            *,
            check_name: str,
            matched: bool,
            expected_summary: str | None,
            actual_summary: str | None,
            detail: str,
        ) -> None:
            checks.append(
                ReplaySemanticCheckSummary(
                    check_name=check_name,
                    matched=matched,
                    expected_summary=expected_summary,
                    actual_summary=actual_summary,
                    detail=detail,
                )
            )
            if not matched:
                mismatch_messages.append(
                    f"semantic {check_name} mismatch: expected {expected_summary or 'none'}, got {actual_summary or 'none'}"
                )

        baseline_service_id = str(baseline_state.get("service_id") or "").strip()
        replay_service_id = str(replay_state.get("service_id") or "").strip()
        add_check(
            check_name="service_id",
            matched=baseline_service_id == replay_service_id,
            expected_summary=baseline_service_id or "none",
            actual_summary=replay_service_id or "none",
            detail="Service identifier should remain stable across baseline and replay.",
        )

        baseline_incident_status = str(baseline_state.get("incident_status") or "").strip()
        replay_incident_status = str(replay_state.get("incident_status") or "").strip()
        add_check(
            check_name="incident_status",
            matched=baseline_incident_status == replay_incident_status,
            expected_summary=baseline_incident_status or "none",
            actual_summary=replay_incident_status or "none",
            detail="Incident status should resolve to the same value.",
        )

        baseline_fact_set_version = int(baseline_state.get("current_fact_set_version") or 0)
        replay_fact_set_version = int(replay_state.get("current_fact_set_version") or 0)
        add_check(
            check_name="fact_set_version",
            matched=baseline_fact_set_version == replay_fact_set_version,
            expected_summary=str(baseline_fact_set_version),
            actual_summary=str(replay_fact_set_version),
            detail="Fact-set version should remain stable for the same replay seed.",
        )

        baseline_hypothesis_titles = cls._state_hypothesis_titles(baseline_state)
        replay_hypothesis_titles = cls._state_hypothesis_titles(replay_state)
        expected_top_hypotheses = cls._normalize_semantic_list(baseline_hypothesis_titles[:3])
        actual_top_hypotheses = cls._normalize_semantic_list(replay_hypothesis_titles[:3])
        top_hypothesis_hit_count = sum(
            1 for title in expected_top_hypotheses if title in set(actual_top_hypotheses)
        )
        top_hypothesis_hit_rate = cls._safe_match_rate(
            top_hypothesis_hit_count,
            len(expected_top_hypotheses),
        )
        add_check(
            check_name="top_hypotheses",
            matched=top_hypothesis_hit_count == len(expected_top_hypotheses),
            expected_summary=cls._summarize_semantic_values(baseline_hypothesis_titles[:3]),
            actual_summary=cls._summarize_semantic_values(replay_hypothesis_titles[:3]),
            detail=(
                f"Top-hypothesis overlap {top_hypothesis_hit_count}/{len(expected_top_hypotheses)}."
            ),
        )

        baseline_recommendations = cls._state_recommendation_titles(baseline_state)
        replay_recommendations = cls._state_recommendation_titles(replay_state)
        normalized_baseline_recommendations = cls._normalize_semantic_list(baseline_recommendations)
        normalized_replay_recommendations = cls._normalize_semantic_list(replay_recommendations)
        recommendation_match_count = sum(
            1 for title in normalized_baseline_recommendations if title in set(normalized_replay_recommendations)
        )
        recommendation_match_rate = cls._safe_match_rate(
            recommendation_match_count,
            len(normalized_baseline_recommendations),
        )
        add_check(
            check_name="recommendations",
            matched=recommendation_match_count == len(normalized_baseline_recommendations),
            expected_summary=cls._summarize_semantic_values(baseline_recommendations),
            actual_summary=cls._summarize_semantic_values(replay_recommendations),
            detail=(
                f"Recommendation title overlap {recommendation_match_count}/{len(normalized_baseline_recommendations)}."
            ),
        )

        baseline_comms = cls._state_comms_fingerprints(baseline_state)
        replay_comms = cls._state_comms_fingerprints(replay_state)
        normalized_baseline_comms = cls._normalize_semantic_list(baseline_comms)
        normalized_replay_comms = cls._normalize_semantic_list(replay_comms)
        comms_match_count = sum(
            1 for fingerprint in normalized_baseline_comms if fingerprint in set(normalized_replay_comms)
        )
        comms_match_rate = cls._safe_match_rate(comms_match_count, len(normalized_baseline_comms))
        add_check(
            check_name="comms_drafts",
            matched=comms_match_count == len(normalized_baseline_comms),
            expected_summary=cls._summarize_semantic_values(baseline_comms),
            actual_summary=cls._summarize_semantic_values(replay_comms),
            detail=f"Communication draft overlap {comms_match_count}/{len(normalized_baseline_comms)}.",
        )

        baseline_postmortem_markdown = cls._normalize_semantic_text(baseline_state.get("postmortem_markdown"))
        replay_postmortem_markdown = cls._normalize_semantic_text(replay_state.get("postmortem_markdown"))
        postmortem_present_expected = bool(baseline_postmortem_markdown)
        postmortem_present_actual = bool(replay_postmortem_markdown)
        postmortem_markdown_matched: bool | None = None
        if postmortem_present_expected or postmortem_present_actual:
            postmortem_markdown_matched = baseline_postmortem_markdown == replay_postmortem_markdown
            add_check(
                check_name="postmortem_markdown",
                matched=bool(postmortem_markdown_matched),
                expected_summary=cls._truncate_semantic_value(str(baseline_state.get("postmortem_markdown") or ""), limit=100)
                or "none",
                actual_summary=cls._truncate_semantic_value(str(replay_state.get("postmortem_markdown") or ""), limit=100)
                or "none",
                detail="Postmortem markdown should remain stable when retrospective output is replayed.",
            )

        semantic_check_count = len(checks)
        semantic_mismatch_count = sum(1 for item in checks if not item.matched)
        semantic_match_rate = cls._safe_match_rate(
            semantic_check_count - semantic_mismatch_count,
            semantic_check_count,
        )
        return {
            "semantic_check_count": semantic_check_count,
            "semantic_mismatch_count": semantic_mismatch_count,
            "semantic_match_rate": semantic_match_rate,
            "service_id_mismatch_count": int(baseline_service_id != replay_service_id),
            "incident_status_mismatch_count": int(baseline_incident_status != replay_incident_status),
            "fact_set_version_mismatch_count": int(baseline_fact_set_version != replay_fact_set_version),
            "top_hypothesis_expected_count": len(expected_top_hypotheses),
            "top_hypothesis_actual_count": len(actual_top_hypotheses),
            "top_hypothesis_hit_count": top_hypothesis_hit_count,
            "top_hypothesis_hit_rate": top_hypothesis_hit_rate,
            "recommendation_expected_count": len(normalized_baseline_recommendations),
            "recommendation_actual_count": len(normalized_replay_recommendations),
            "recommendation_match_count": recommendation_match_count,
            "recommendation_match_rate": recommendation_match_rate,
            "comms_expected_count": len(normalized_baseline_comms),
            "comms_actual_count": len(normalized_replay_comms),
            "comms_match_count": comms_match_count,
            "comms_match_rate": comms_match_rate,
            "postmortem_present_expected": postmortem_present_expected,
            "postmortem_present_actual": postmortem_present_actual,
            "postmortem_markdown_matched": postmortem_markdown_matched,
            "semantic_checks": checks,
            "semantic_mismatch_messages": mismatch_messages,
        }

    def list_workflows(self):
        return self.workflow_api_service.list_workflows()

    @staticmethod
    def _runtime_provider_is_remote_active(effective_mode: str | None) -> bool:
        return runtime_admin_runtime_provider_is_remote_active(effective_mode)

    @classmethod
    def _build_runtime_provider_alert_summary(
        cls,
        *,
        model_provider: dict[str, Any],
        tooling: dict[str, dict[str, Any]],
    ) -> RuntimeProviderAlertSummary:
        return runtime_admin_build_runtime_provider_alert_summary(
            model_provider=model_provider,
            tooling=tooling,
        )

    def _build_remote_provider_smoke_alert_summary(
        self,
        *,
        smoke_summary: RemoteProviderSmokeHistorySummary,
    ) -> RemoteProviderSmokeAlertSummary:
        return runtime_admin_build_remote_provider_smoke_alert_summary(
            self,
            smoke_summary=smoke_summary,
        )

    def get_runtime_capabilities(self) -> RuntimeCapabilitiesResponse:
        return runtime_admin_get_runtime_capabilities(self)

    def run_remote_provider_smoke(
        self,
        command: RemoteProviderSmokeCommand | dict[str, Any],
        *,
        auth_context=None,
        request_id: str | None = None,
    ) -> RemoteProviderSmokeResponse:
        return runtime_admin_run_remote_provider_smoke(
            self,
            command,
            auth_context=auth_context,
            request_id=request_id,
        )

    def list_remote_provider_smoke_runs(
        self,
        *,
        limit: int = 10,
        actor_user_id: str | None = None,
        request_id: str | None = None,
        provider: str | None = None,
    ) -> list[RemoteProviderSmokeRunRecord]:
        return runtime_admin_list_remote_provider_smoke_runs(
            self,
            limit=limit,
            actor_user_id=actor_user_id,
            request_id=request_id,
            provider=provider,
        )

    def summarize_remote_provider_smoke_runs(
        self,
        *,
        limit: int = 50,
        actor_user_id: str | None = None,
        request_id: str | None = None,
        provider: str | None = None,
    ) -> RemoteProviderSmokeHistorySummary:
        return runtime_admin_summarize_remote_provider_smoke_runs(
            self,
            limit=limit,
            actor_user_id=actor_user_id,
            request_id=request_id,
            provider=provider,
        )

    def get_health_status(self) -> HealthResponse:
        return runtime_admin_get_health_status(self)

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
            draft_node_name = (
                "comms_published_from_approval"
                if published_draft.status == "published"
                else (
                    "comms_delivery_accepted_from_approval"
                    if published_draft.status == "accepted"
                    else "comms_publish_failed_from_approval"
                )
            )
            self._emit_incident_event(
                incident_id=approval_task.incident_id,
                event_name="opsgraph.comms.updated",
                aggregate_type="comms_draft",
                aggregate_id=published_draft.draft_id,
                node_name=draft_node_name,
                payload={
                    "draft_id": published_draft.draft_id,
                    "comms_status": published_draft.status,
                    "published_message_ref": published_draft.published_message_ref,
                    "delivery_state": published_draft.delivery_state,
                    "delivery_confirmed": published_draft.delivery_confirmed,
                    "provider_delivery_status": published_draft.provider_delivery_status,
                    "published_at": (
                        published_draft.published_at.isoformat()
                        if published_draft.published_at is not None
                        else None
                    ),
                    "delivery_error": published_draft.delivery_error,
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
        node_name = (
            "comms_published"
            if response.status == "published"
            else ("comms_delivery_accepted" if response.status == "accepted" else "comms_publish_failed")
        )
        self._emit_incident_event(
            incident_id=incident_id,
            event_name="opsgraph.comms.updated",
            aggregate_type="comms_draft",
            aggregate_id=draft_id,
            node_name=node_name,
            payload={
                "draft_id": draft_id,
                "comms_status": response.status,
                "published_message_ref": response.published_message_ref,
                "delivery_state": response.delivery_state,
                "delivery_confirmed": response.delivery_confirmed,
                "provider_delivery_status": response.provider_delivery_status,
                "published_at": response.published_at.isoformat() if response.published_at is not None else None,
                "delivery_error": response.delivery_error,
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

    def get_replay_quality_summary(
        self,
        workspace_id: str,
        incident_id: str | None = None,
    ) -> ReplayQualitySummary:
        incidents = self.list_incidents(workspace_id)
        if incident_id is not None:
            incidents = [item for item in incidents if item.incident_id == incident_id]
        replay_cases = self.list_replay_cases(workspace_id, incident_id)
        replay_case_expected_output_count = sum(
            1
            for item in replay_cases
            if self.get_replay_case(item.replay_case_id).expected_output is not None
        )
        baselines = self.list_replay_baselines(workspace_id, incident_id)
        evaluations = self.list_replay_evaluations(workspace_id, incident_id)
        matched_evaluation_count = sum(1 for item in evaluations if item.status == "matched")
        semantic_evaluations = [item for item in evaluations if item.semantic_check_count > 0]
        latest_evaluation = evaluations[0] if evaluations else None
        return ReplayQualitySummary(
            workspace_id=workspace_id,
            incident_id=incident_id,
            incident_count=len(incidents),
            replay_case_count=len(replay_cases),
            replay_case_expected_output_count=replay_case_expected_output_count,
            replay_case_expected_output_coverage_rate=round(
                replay_case_expected_output_count / len(replay_cases),
                4,
            )
            if replay_cases
            else 0.0,
            baseline_count=len(baselines),
            baseline_incident_coverage_count=len({item.incident_id for item in baselines}),
            baseline_coverage_rate=round(
                len({item.incident_id for item in baselines}) / len(incidents),
                4,
            )
            if incidents
            else 0.0,
            evaluation_count=len(evaluations),
            matched_evaluation_count=matched_evaluation_count,
            mismatched_evaluation_count=(len(evaluations) - matched_evaluation_count),
            replay_pass_rate=round(matched_evaluation_count / len(evaluations), 4) if evaluations else 0.0,
            avg_replay_score=self._average_or_none([item.score for item in evaluations]),
            semantic_evaluation_count=len(semantic_evaluations),
            avg_semantic_match_rate=self._average_or_none(
                [item.semantic_match_rate for item in semantic_evaluations]
            ),
            avg_top_hypothesis_hit_rate=self._average_or_none(
                [item.top_hypothesis_hit_rate for item in semantic_evaluations]
            ),
            avg_recommendation_match_rate=self._average_or_none(
                [item.recommendation_match_rate for item in semantic_evaluations]
            ),
            avg_comms_match_rate=self._average_or_none(
                [item.comms_match_rate for item in semantic_evaluations]
            ),
            latest_report_id=(latest_evaluation.report_id if latest_evaluation is not None else None),
            latest_report_created_at=(
                latest_evaluation.created_at if latest_evaluation is not None else None
            ),
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
        baseline_state = self.get_workflow_state(baseline.workflow_run_id)
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
        semantic_metrics = self._build_replay_semantic_metrics(
            baseline_state=baseline_state.raw_state,
            replay_state=replay_state.raw_state,
        )
        mismatches.extend(semantic_metrics["semantic_mismatch_messages"])
        status = "matched" if not mismatches else "mismatched"
        max_checks = max(
            1,
            2
            + max(len(baseline.node_summaries), len(replay_nodes)) * 3
            + int(semantic_metrics["semantic_check_count"]),
        )
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
        evaluation = evaluation.model_copy(
            update={
                key: value
                for key, value in semantic_metrics.items()
                if key != "semantic_mismatch_messages"
            }
        )
        artifact_path = write_replay_report_artifacts(
            report_id=evaluation.report_id,
            payload={
                "baseline": baseline.model_dump(mode="json"),
                "baseline_workflow_state": baseline_state.model_dump(mode="json"),
                "replay": replay.model_dump(mode="json"),
                "replay_workflow_state": replay_state.model_dump(mode="json"),
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
        seed = self.repository.get_incident_execution_seed(command.incident_id)
        response, run_result = self._run_registered_workflow(
            workflow_name="opsgraph_incident_response",
            workflow_run_id=command.workflow_run_id,
            input_payload={
                "incident_id": command.incident_id,
                "ops_workspace_id": command.ops_workspace_id,
                "signal_ids": command.signal_ids,
                "signal_summaries": command.signal_summaries,
                "current_incident_candidates": command.current_incident_candidates,
                "context_bundle_id": command.context_bundle_id or seed.get("context_bundle_id"),
                "context_missing_sources": (
                    command.context_missing_sources
                    or list(seed.get("context_missing_sources", []))
                ),
                "current_fact_set_version": command.current_fact_set_version,
                "service_id": command.service_id,
                "confirmed_fact_refs": command.confirmed_fact_refs,
                "top_hypothesis_refs": command.top_hypothesis_refs,
                "investigation_memory_context": (
                    command.investigation_memory_context
                    or list(seed.get("investigation_memory_context", []))
                ),
                "recommendation_memory_context": (
                    command.recommendation_memory_context
                    or list(seed.get("recommendation_memory_context", []))
                ),
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
                "postmortem_memory_context": (
                    command.postmortem_memory_context
                    or self.repository.get_postmortem_memory_context(command.incident_id)
                ),
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
