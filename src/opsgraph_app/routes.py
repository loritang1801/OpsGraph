from __future__ import annotations

import importlib

ERROR_STATUS_BY_CODE = {
    "CONFLICT_STALE_RESOURCE": 409,
    "FACT_VERSION_CONFLICT": 409,
    "HYPOTHESIS_STATUS_CONFLICT": 409,
    "RECOMMENDATION_STATUS_CONFLICT": 409,
    "APPROVAL_REQUIRED": 409,
    "COMM_DRAFT_STALE_FACT_SET": 409,
    "COMM_DRAFT_ALREADY_PUBLISHED": 409,
    "INCIDENT_ALREADY_RESOLVED": 409,
    "INCIDENT_NOT_RESOLVED": 409,
    "REPLAY_RUN_NOT_EXECUTED": 409,
    "ROOT_CAUSE_FACT_REQUIRED": 422,
    "REPLAY_EVALUATION_UNAVAILABLE": 503,
}

from .api_models import (
    AlertIngestCommand,
    AlertIngestResponse,
    ApprovalTaskSummary,
    CloseIncidentCommand,
    CommsDraftSummary,
    CommsPublishCommand,
    CommsPublishResponse,
    FactCreateCommand,
    FactMutationResponse,
    FactRetractCommand,
    FactSummary,
    HealthResponse,
    HypothesisDecisionCommand,
    HypothesisDecisionResponse,
    HypothesisSummary,
    IncidentResponseCommand,
    IncidentSummary,
    IncidentWorkspaceResponse,
    RecommendationSummary,
    ReplayBaselineCaptureCommand,
    ReplayBaselineSummary,
    ReplayEvaluationCommand,
    ReplayEvaluationSummary,
    RecommendationDecisionCommand,
    RecommendationDecisionResponse,
    OpsGraphRunResponse,
    OpsGraphWorkflowStateResponse,
    PostmortemSummary,
    ReplayCaseDetail,
    ReplayCaseSummary,
    ReplayRunCommand,
    ReplayStatusCommand,
    ReplayRunSummary,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    SeverityOverrideCommand,
)
from .service import OpsGraphAppService
from .shared_runtime import load_shared_agent_platform


def map_domain_error(exc: Exception, *, path: str = "") -> tuple[int, dict[str, object]]:
    if isinstance(exc, KeyError):
        code = "INCIDENT_NOT_FOUND" if "/incidents/" in path else "RESOURCE_NOT_FOUND"
        resource_id = str(exc.args[0]) if exc.args else "resource"
        return 404, {"error": {"code": code, "message": f"{code}: {resource_id}"}}
    if isinstance(exc, ValueError):
        code = str(exc)
        status_code = ERROR_STATUS_BY_CODE.get(code, 400)
        return status_code, {"error": {"code": code, "message": code}}
    return 500, {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}


def create_fastapi_app(service: OpsGraphAppService):
    ap = load_shared_agent_platform()
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        errors_module = importlib.import_module(f"{ap.__name__}.errors")
        FastAPIUnavailableError = errors_module.FastAPIUnavailableError

        raise FastAPIUnavailableError("fastapi is not installed") from exc

    app = FastAPI(title="OpsGraph API")

    @app.exception_handler(KeyError)
    def handle_key_error(request: Request, exc: KeyError):
        status_code, payload = map_domain_error(exc, path=str(request.url.path))
        return JSONResponse(status_code=status_code, content=payload)

    @app.exception_handler(ValueError)
    def handle_value_error(request: Request, exc: ValueError):
        status_code, payload = map_domain_error(exc, path=str(request.url.path))
        return JSONResponse(status_code=status_code, content=payload)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", product="opsgraph")

    @app.get("/api/v1/workflows")
    def list_workflows():
        return service.list_workflows()

    @app.get("/api/v1/workflows/{workflow_run_id}", response_model=OpsGraphWorkflowStateResponse)
    def get_workflow_state(workflow_run_id: str) -> OpsGraphWorkflowStateResponse:
        return service.get_workflow_state(workflow_run_id)

    @app.post("/api/v1/opsgraph/alerts/prometheus", response_model=AlertIngestResponse, status_code=202)
    def ingest_prometheus_alert(command: AlertIngestCommand) -> AlertIngestResponse:
        return service.ingest_alert(command)

    @app.post("/api/v1/opsgraph/alerts/grafana", response_model=AlertIngestResponse, status_code=202)
    def ingest_grafana_alert(command: AlertIngestCommand) -> AlertIngestResponse:
        payload = command.model_dump()
        payload["source"] = "grafana"
        return service.ingest_alert(payload)

    @app.get("/api/v1/opsgraph/incidents", response_model=list[IncidentSummary])
    def list_incidents(
        workspace_id: str,
        status: str | None = None,
        severity: str | None = None,
        service_id: str | None = None,
    ) -> list[IncidentSummary]:
        return service.list_incidents(
            workspace_id,
            status=status,
            severity=severity,
            service_id=service_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}", response_model=IncidentWorkspaceResponse)
    def get_incident_workspace(incident_id: str) -> IncidentWorkspaceResponse:
        return service.get_incident_workspace(incident_id)

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/hypotheses")
    def list_hypotheses(incident_id: str) -> list[HypothesisSummary]:
        return service.list_hypotheses(incident_id)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/facts", response_model=FactMutationResponse)
    def add_fact(incident_id: str, command: FactCreateCommand) -> FactMutationResponse:
        return service.add_fact(incident_id, command)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/facts/{fact_id}/retract", response_model=FactMutationResponse)
    def retract_fact(incident_id: str, fact_id: str, command: FactRetractCommand) -> FactMutationResponse:
        return service.retract_fact(incident_id, fact_id, command)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/severity", response_model=IncidentSummary)
    def override_severity(incident_id: str, command: SeverityOverrideCommand) -> IncidentSummary:
        return service.override_severity(incident_id, command)

    @app.post(
        "/api/v1/opsgraph/incidents/{incident_id}/hypotheses/{hypothesis_id}/decision",
        response_model=HypothesisDecisionResponse,
    )
    def decide_hypothesis(
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand,
    ) -> HypothesisDecisionResponse:
        return service.decide_hypothesis(incident_id, hypothesis_id, command)

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/recommendations")
    def list_recommendations(incident_id: str) -> list[RecommendationSummary]:
        return service.list_recommendations(incident_id)

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/approval-tasks", response_model=list[ApprovalTaskSummary])
    def list_approval_tasks(incident_id: str) -> list[ApprovalTaskSummary]:
        return service.list_approval_tasks(incident_id)

    @app.get("/api/v1/opsgraph/approval-tasks/{approval_task_id}", response_model=ApprovalTaskSummary)
    def get_approval_task(approval_task_id: str) -> ApprovalTaskSummary:
        return service.get_approval_task(approval_task_id)

    @app.post(
        "/api/v1/opsgraph/incidents/{incident_id}/recommendations/{recommendation_id}/decision",
        response_model=RecommendationDecisionResponse,
    )
    def decide_recommendation(
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand,
    ) -> RecommendationDecisionResponse:
        return service.decide_recommendation(incident_id, recommendation_id, command)

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/comms")
    def list_comms(
        incident_id: str,
        channel: str | None = None,
        status: str | None = None,
    ) -> list[CommsDraftSummary]:
        return service.list_comms(incident_id, channel=channel, status=status)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/comms/{draft_id}/publish", response_model=CommsPublishResponse)
    def publish_comms(
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand,
    ) -> CommsPublishResponse:
        return service.publish_comms(incident_id, draft_id, command)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/resolve", response_model=IncidentSummary)
    def resolve_incident(incident_id: str, command: ResolveIncidentCommand) -> IncidentSummary:
        return service.resolve_incident(incident_id, command)

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/close", response_model=IncidentSummary)
    def close_incident(incident_id: str, command: CloseIncidentCommand) -> IncidentSummary:
        return service.close_incident(incident_id, command)

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/postmortem", response_model=PostmortemSummary)
    def get_postmortem(incident_id: str) -> PostmortemSummary:
        return service.get_postmortem(incident_id)

    @app.get("/api/v1/opsgraph/replay-cases", response_model=list[ReplayCaseSummary])
    def list_replay_cases(
        workspace_id: str,
        incident_id: str | None = None,
    ) -> list[ReplayCaseSummary]:
        return service.list_replay_cases(workspace_id, incident_id)

    @app.get("/api/v1/opsgraph/replay-cases/{replay_case_id}", response_model=ReplayCaseDetail)
    def get_replay_case(replay_case_id: str) -> ReplayCaseDetail:
        return service.get_replay_case(replay_case_id)

    @app.post("/api/v1/opsgraph/incidents/respond", response_model=OpsGraphRunResponse)
    def respond_to_incident(command: IncidentResponseCommand) -> OpsGraphRunResponse:
        return service.respond_to_incident(command)

    @app.post("/api/v1/opsgraph/incidents/retrospective", response_model=OpsGraphRunResponse)
    def build_retrospective(command: RetrospectiveCommand) -> OpsGraphRunResponse:
        return service.build_retrospective(command)

    @app.post("/api/v1/opsgraph/replays/run", response_model=ReplayRunSummary, status_code=202)
    def start_replay_run(command: ReplayRunCommand) -> ReplayRunSummary:
        return service.start_replay_run(command)

    @app.get("/api/v1/opsgraph/replays", response_model=list[ReplayRunSummary])
    def list_replays(
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
        status: str | None = None,
    ) -> list[ReplayRunSummary]:
        return service.list_replays(workspace_id, incident_id, replay_case_id, status)

    @app.get("/api/v1/opsgraph/replays/baselines")
    def list_replay_baselines(
        workspace_id: str,
        incident_id: str | None = None,
    ) -> list[ReplayBaselineSummary]:
        return service.list_replay_baselines(workspace_id, incident_id)

    @app.post("/api/v1/opsgraph/replays/baselines/capture", response_model=ReplayBaselineSummary)
    def capture_replay_baseline(command: ReplayBaselineCaptureCommand) -> ReplayBaselineSummary:
        return service.capture_replay_baseline(command)

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/status", response_model=ReplayRunSummary)
    def update_replay_status(replay_run_id: str, command: ReplayStatusCommand) -> ReplayRunSummary:
        return service.update_replay_status(replay_run_id, command)

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/execute", response_model=ReplayRunSummary)
    def execute_replay_run(replay_run_id: str) -> ReplayRunSummary:
        return service.execute_replay_run(replay_run_id)

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/evaluate", response_model=ReplayEvaluationSummary)
    def evaluate_replay_run(
        replay_run_id: str,
        command: ReplayEvaluationCommand,
    ) -> ReplayEvaluationSummary:
        return service.evaluate_replay_run(replay_run_id, command)

    @app.get("/api/v1/opsgraph/replays/reports")
    def list_replay_reports(
        workspace_id: str,
        incident_id: str | None = None,
        replay_run_id: str | None = None,
        replay_case_id: str | None = None,
    ) -> list[ReplayEvaluationSummary]:
        return service.list_replay_evaluations(workspace_id, incident_id, replay_run_id, replay_case_id)

    return app
