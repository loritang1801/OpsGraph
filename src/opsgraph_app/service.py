from __future__ import annotations

from typing import Any, TypeVar

from .api_models import (
    AlertIngestCommand,
    AlertIngestResponse,
    CloseIncidentCommand,
    CommsDraftSummary,
    CommsPublishCommand,
    CommsPublishResponse,
    FactCreateCommand,
    FactMutationResponse,
    FactRetractCommand,
    HypothesisDecisionCommand,
    HypothesisDecisionResponse,
    HypothesisSummary,
    IncidentResponseCommand,
    IncidentSummary,
    IncidentWorkspaceResponse,
    OpsGraphRunResponse,
    OpsGraphWorkflowStateResponse,
    PostmortemSummary,
    ReplayCaseDetail,
    ReplayCaseSummary,
    ReplayBaselineCaptureCommand,
    ReplayBaselineSummary,
    ReplayEvaluationCommand,
    ReplayEvaluationSummary,
    ReplayNodeDiffSummary,
    ReplayNodeSummary,
    RecommendationDecisionCommand,
    RecommendationDecisionResponse,
    ReplayRunCommand,
    ReplayStatusCommand,
    ReplayRunSummary,
    RecommendationSummary,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    SeverityOverrideCommand,
)
from .repository import OpsGraphRepository
from .replay_fixtures import seed_incident_response_replay_fixtures
from .replay_reports import write_replay_report_artifacts

CommandT = TypeVar("CommandT", IncidentResponseCommand, RetrospectiveCommand)


class OpsGraphAppService:
    def __init__(
        self,
        workflow_api_service,
        repository: OpsGraphRepository,
        runtime_stores=None,
        shared_platform=None,
        workflow_registry=None,
        prompt_service=None,
        replay_fixture_store=None,
    ) -> None:
        self.workflow_api_service = workflow_api_service
        self.repository = repository
        self.runtime_stores = runtime_stores
        self.shared_platform = shared_platform
        self.workflow_registry = workflow_registry
        self.prompt_service = prompt_service
        self.replay_fixture_store = replay_fixture_store

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

    def list_workflows(self):
        return self.workflow_api_service.list_workflows()

    def list_incidents(self, workspace_id: str) -> list[IncidentSummary]:
        return self.repository.list_incidents(workspace_id)

    def get_incident_workspace(self, incident_id: str) -> IncidentWorkspaceResponse:
        return self.repository.get_incident_workspace(incident_id)

    def list_hypotheses(self, incident_id: str) -> list[HypothesisSummary]:
        return self.repository.list_hypotheses(incident_id)

    def list_recommendations(self, incident_id: str) -> list[RecommendationSummary]:
        return self.repository.list_recommendations(incident_id)

    def list_comms(self, incident_id: str) -> list[CommsDraftSummary]:
        return self.repository.list_comms(incident_id)

    def add_fact(
        self,
        incident_id: str,
        command: FactCreateCommand | dict[str, Any],
    ) -> FactMutationResponse:
        if isinstance(command, dict):
            command = FactCreateCommand.model_validate(command)
        return self.repository.add_fact(incident_id, command)

    def retract_fact(
        self,
        incident_id: str,
        fact_id: str,
        command: FactRetractCommand | dict[str, Any],
    ) -> FactMutationResponse:
        if isinstance(command, dict):
            command = FactRetractCommand.model_validate(command)
        return self.repository.retract_fact(incident_id, fact_id, command)

    def override_severity(
        self,
        incident_id: str,
        command: SeverityOverrideCommand | dict[str, Any],
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = SeverityOverrideCommand.model_validate(command)
        return self.repository.override_severity(incident_id, command)

    def decide_hypothesis(
        self,
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand | dict[str, Any],
    ) -> HypothesisDecisionResponse:
        if isinstance(command, dict):
            command = HypothesisDecisionCommand.model_validate(command)
        return self.repository.decide_hypothesis(incident_id, hypothesis_id, command)

    def decide_recommendation(
        self,
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand | dict[str, Any],
    ) -> RecommendationDecisionResponse:
        if isinstance(command, dict):
            command = RecommendationDecisionCommand.model_validate(command)
        return self.repository.decide_recommendation(incident_id, recommendation_id, command)

    def publish_comms(
        self,
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand | dict[str, Any],
    ) -> CommsPublishResponse:
        if isinstance(command, dict):
            command = CommsPublishCommand.model_validate(command)
        return self.repository.publish_comms(incident_id, draft_id, command)

    def resolve_incident(
        self,
        incident_id: str,
        command: ResolveIncidentCommand | dict[str, Any],
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = ResolveIncidentCommand.model_validate(command)
        return self.repository.resolve_incident(incident_id, command)

    def close_incident(
        self,
        incident_id: str,
        command: CloseIncidentCommand | dict[str, Any],
    ) -> IncidentSummary:
        if isinstance(command, dict):
            command = CloseIncidentCommand.model_validate(command)
        return self.repository.close_incident(incident_id, command)

    def get_postmortem(self, incident_id: str) -> PostmortemSummary:
        return self.repository.get_postmortem(incident_id)

    def start_replay_run(self, command: ReplayRunCommand | dict[str, Any]) -> ReplayRunSummary:
        if isinstance(command, dict):
            command = ReplayRunCommand.model_validate(command)
        return self.repository.start_replay_run(command)

    def list_replays(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
    ) -> list[ReplayRunSummary]:
        return self.repository.list_replays(
            workspace_id,
            incident_id,
            replay_case_id,
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
    ) -> ReplayRunSummary:
        if isinstance(command, dict):
            command = ReplayStatusCommand.model_validate(command)
        return self.repository.update_replay_status(replay_run_id, command)

    def execute_replay_run(self, replay_run_id: str) -> ReplayRunSummary:
        replay = self.repository.mark_replay_execution(replay_run_id, status="running")
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
            )
        return self.repository.mark_replay_execution(
            replay_run_id,
            status="completed",
            workflow_run_id=result.workflow_run_id,
            current_state=result.current_state,
            error_message=None,
        )

    def capture_replay_baseline(
        self,
        command: ReplayBaselineCaptureCommand | dict[str, Any],
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
        )

    def evaluate_replay_run(
        self,
        replay_run_id: str,
        command: ReplayEvaluationCommand | dict[str, Any],
    ) -> ReplayEvaluationSummary:
        if isinstance(command, dict):
            command = ReplayEvaluationCommand.model_validate(command)
        if self.runtime_stores is None:
            raise ValueError("Replay evaluation requires runtime stores")
        replay = self.repository.get_replay_run(replay_run_id)
        baseline = self.repository.get_replay_baseline(command.baseline_id)
        if replay.workflow_run_id is None:
            raise ValueError("Replay run has not executed yet")
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
        )

    def ingest_alert(self, command: AlertIngestCommand | dict[str, Any]) -> AlertIngestResponse:
        if isinstance(command, dict):
            command = AlertIngestCommand.model_validate(command)
        return self.repository.ingest_alert(
            ops_workspace_id=command.ops_workspace_id,
            correlation_key=command.correlation_key,
            summary=command.summary,
            observed_at=command.observed_at,
            source=command.source,
        )

    def respond_to_incident(self, command: IncidentResponseCommand | dict[str, Any]) -> OpsGraphRunResponse:
        command = self._coerce_command(command, IncidentResponseCommand)
        result = self.workflow_api_service.start_workflow(
            {
                "workflow_name": "opsgraph_incident_response",
                "workflow_run_id": command.workflow_run_id,
                "input_payload": {
                    "incident_id": command.incident_id,
                    "ops_workspace_id": command.ops_workspace_id,
                    "signal_ids": command.signal_ids,
                    "signal_summaries": command.signal_summaries,
                    "current_incident_candidates": command.current_incident_candidates,
                    "context_bundle_id": command.context_bundle_id,
                    "current_fact_set_version": command.current_fact_set_version,
                    "confirmed_fact_refs": command.confirmed_fact_refs,
                    "top_hypothesis_refs": command.top_hypothesis_refs,
                    "target_channels": command.target_channels,
                    "organization_id": command.organization_id,
                    "workspace_id": command.workspace_id,
                },
                "state_overrides": command.state_overrides,
            }
        )
        response = self._to_run_response(result)
        self.repository.record_incident_response_result(
            incident_id=command.incident_id,
            workflow_run_id=response.workflow_run_id,
            checkpoint_seq=response.checkpoint_seq,
        )
        return response

    def build_retrospective(self, command: RetrospectiveCommand | dict[str, Any]) -> OpsGraphRunResponse:
        command = self._coerce_command(command, RetrospectiveCommand)
        result = self.workflow_api_service.start_workflow(
            {
                "workflow_name": "opsgraph_retrospective",
                "workflow_run_id": command.workflow_run_id,
                "input_payload": {
                    "incident_id": command.incident_id,
                    "ops_workspace_id": command.ops_workspace_id,
                    "current_fact_set_version": command.current_fact_set_version,
                    "confirmed_fact_refs": command.confirmed_fact_refs,
                    "timeline_refs": command.timeline_refs,
                    "resolution_summary": command.resolution_summary,
                    "organization_id": command.organization_id,
                    "workspace_id": command.workspace_id,
                },
                "state_overrides": command.state_overrides,
            }
        )
        response = self._to_run_response(result)
        self.repository.record_retrospective_result(
            incident_id=command.incident_id,
            workflow_run_id=response.workflow_run_id,
            checkpoint_seq=response.checkpoint_seq,
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
