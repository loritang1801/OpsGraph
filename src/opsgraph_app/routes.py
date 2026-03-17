from __future__ import annotations

import base64
import binascii
import importlib
from typing import Any

ERROR_STATUS_BY_CODE = {
    "CONFLICT_STALE_RESOURCE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
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
    "INVALID_CURSOR": 400,
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

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


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


def _serialize_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if isinstance(value, list):
        return [_serialize_data(item) for item in value]
    return value


def success_envelope(
    data: Any,
    *,
    request_id: str | None = None,
    workflow_run_id: str | None = None,
    next_cursor: str | None = None,
    has_more: bool = False,
) -> dict[str, object]:
    meta: dict[str, object] = {"request_id": request_id, "has_more": has_more}
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    if workflow_run_id is not None:
        meta["workflow_run_id"] = workflow_run_id
    return {"data": _serialize_data(data), "meta": meta}


def _encode_cursor(offset: int) -> str | None:
    if offset <= 0:
        return None
    return base64.urlsafe_b64encode(f"offset:{offset}".encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> int:
    if cursor in {None, ""}:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("INVALID_CURSOR") from exc
    prefix, separator, raw_offset = decoded.partition(":")
    if prefix != "offset" or separator != ":" or not raw_offset.isdigit():
        raise ValueError("INVALID_CURSOR")
    return int(raw_offset)


def paginate_collection(items: list[Any], *, cursor: str | None = None, limit: int = DEFAULT_PAGE_LIMIT) -> tuple[list[Any], str | None, bool]:
    normalized_limit = max(1, min(limit, MAX_PAGE_LIMIT))
    start = _decode_cursor(cursor)
    page = items[start : start + normalized_limit]
    next_offset = start + normalized_limit
    has_more = next_offset < len(items)
    return page, (_encode_cursor(next_offset) if has_more else None), has_more


def create_fastapi_app(service: OpsGraphAppService):
    ap = load_shared_agent_platform()
    try:
        from fastapi import FastAPI, Header, Request
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

    @app.post("/api/v1/opsgraph/alerts/prometheus", status_code=202)
    def ingest_prometheus_alert(
        command: AlertIngestCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        response = service.ingest_alert(command, idempotency_key=idempotency_key)
        return success_envelope(
            {
                "accepted_signals": response.accepted_signals,
                "incident_id": response.incident_id,
                "incident_created": response.incident_created,
                "signal_id": response.signal_id,
            },
            request_id=request_id,
            workflow_run_id=response.workflow_run_id,
        )

    @app.post("/api/v1/opsgraph/alerts/grafana", status_code=202)
    def ingest_grafana_alert(
        command: AlertIngestCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        payload = command.model_dump()
        payload["source"] = "grafana"
        response = service.ingest_alert(payload, idempotency_key=idempotency_key)
        return success_envelope(
            {
                "accepted_signals": response.accepted_signals,
                "incident_id": response.incident_id,
                "incident_created": response.incident_created,
                "signal_id": response.signal_id,
            },
            request_id=request_id,
            workflow_run_id=response.workflow_run_id,
        )

    @app.get("/api/v1/opsgraph/incidents")
    def list_incidents(
        workspace_id: str,
        status: str | None = None,
        severity: str | None = None,
        service_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_incidents(
            workspace_id,
            status=status,
            severity=severity,
            service_id=service_id,
        )
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}")
    def get_incident_workspace(
        incident_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_incident_workspace(incident_id),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/hypotheses")
    def list_hypotheses(
        incident_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_hypotheses(incident_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/facts")
    def add_fact(
        incident_id: str,
        command: FactCreateCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.add_fact(incident_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/facts/{fact_id}/retract")
    def retract_fact(
        incident_id: str,
        fact_id: str,
        command: FactRetractCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.retract_fact(incident_id, fact_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/severity")
    def override_severity(
        incident_id: str,
        command: SeverityOverrideCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.override_severity(incident_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.post(
        "/api/v1/opsgraph/incidents/{incident_id}/hypotheses/{hypothesis_id}/decision",
        response_model=HypothesisDecisionResponse,
    )
    def decide_hypothesis(
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.decide_hypothesis(
                incident_id,
                hypothesis_id,
                command,
                idempotency_key=idempotency_key,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/recommendations")
    def list_recommendations(
        incident_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_recommendations(incident_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/approval-tasks")
    def list_approval_tasks(
        incident_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_approval_tasks(incident_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/approval-tasks/{approval_task_id}")
    def get_approval_task(
        approval_task_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_approval_task(approval_task_id),
            request_id=request_id,
        )

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
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_comms(incident_id, channel=channel, status=status)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/comms/{draft_id}/publish")
    def publish_comms(
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.publish_comms(incident_id, draft_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/resolve")
    def resolve_incident(
        incident_id: str,
        command: ResolveIncidentCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.resolve_incident(incident_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/close")
    def close_incident(
        incident_id: str,
        command: CloseIncidentCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.close_incident(incident_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/postmortem")
    def get_postmortem(
        incident_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_postmortem(incident_id),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replay-cases")
    def list_replay_cases(
        workspace_id: str,
        incident_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_replay_cases(workspace_id, incident_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/replay-cases/{replay_case_id}")
    def get_replay_case(
        replay_case_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_case(replay_case_id),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/respond", response_model=OpsGraphRunResponse)
    def respond_to_incident(command: IncidentResponseCommand) -> OpsGraphRunResponse:
        return service.respond_to_incident(command)

    @app.post("/api/v1/opsgraph/incidents/retrospective", response_model=OpsGraphRunResponse)
    def build_retrospective(command: RetrospectiveCommand) -> OpsGraphRunResponse:
        return service.build_retrospective(command)

    @app.post("/api/v1/opsgraph/replays/run", status_code=202)
    def start_replay_run(
        command: ReplayRunCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        response = service.start_replay_run(command, idempotency_key=idempotency_key)
        return success_envelope(
            response,
            request_id=request_id,
            workflow_run_id=response.workflow_run_id,
        )

    @app.get("/api/v1/opsgraph/replays")
    def list_replays(
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_replays(workspace_id, incident_id, replay_case_id, status)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/replays/baselines")
    def list_replay_baselines(
        workspace_id: str,
        incident_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_replay_baselines(workspace_id, incident_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

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
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_replay_evaluations(workspace_id, incident_id, replay_run_id, replay_case_id)
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    return app
