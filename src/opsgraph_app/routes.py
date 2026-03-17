from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import json
from datetime import UTC, datetime
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
    "REPLAY_STATUS_CONFLICT": 409,
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
    PostmortemFinalizeCommand,
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


def _event_topic(event_name: str) -> str:
    if event_name.startswith("workflow."):
        return "workflow"
    if event_name.startswith("approval."):
        return "approval"
    if event_name.startswith("artifact."):
        return "artifact"
    if event_name.startswith("opsgraph."):
        return "opsgraph"
    return "workspace"


def _isoformat_utc(value: datetime) -> str:
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _payload_lookup(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _event_topics(context: dict[str, object]) -> set[str]:
    payload = context.get("payload")
    normalized_payload = payload if isinstance(payload, dict) else {}
    topics = {str(context["topic"]), f"opsgraph.workspace.{context['workspace_id']}"}

    incident_id = _payload_lookup(normalized_payload, "incident_id")
    if incident_id is None and str(context["subject_type"]) == "incident":
        incident_id = str(context["subject_id"])
    if incident_id is not None:
        topics.add(f"opsgraph.incident.{incident_id}")

    return topics


def _matches_event_topic(context: dict[str, object], requested_topic: str | None) -> bool:
    if requested_topic is None:
        return True
    return requested_topic in _event_topics(context)


def _normalize_resume_after_id(pending_events: list[Any], resume_after_id: str | None) -> str | None:
    if resume_after_id is None:
        return None
    for stored in pending_events:
        if stored.event.event_id == resume_after_id:
            return resume_after_id
    return None


def _format_sse_message(*, event_id: str, event_name: str, payload: dict[str, object]) -> str:
    return f"id: {event_id}\nevent: {event_name}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


def _resolve_outbox_event_context(service: OpsGraphAppService, event) -> dict[str, object] | None:
    payload = dict(getattr(event, "payload", {}) or {})
    state: dict[str, object] = {}
    runtime_stores = getattr(service, "runtime_stores", None)
    if runtime_stores is not None and hasattr(runtime_stores, "state_store"):
        try:
            state_record = runtime_stores.state_store.load(event.workflow_run_id)
        except Exception:  # noqa: BLE001
            state = {}
        else:
            state = dict(getattr(state_record, "state", {}) or {})
    workspace_id = (
        state.get("workspace_id")
        or state.get("ops_workspace_id")
        or state.get("workspace")
        or payload.get("workspace_id")
        or payload.get("ops_workspace_id")
        or payload.get("workspace")
    )
    if workspace_id is None:
        return None
    subject_type = (
        state.get("subject_type")
        or payload.get("subject_type")
        or ("incident" if payload.get("incident_id") is not None else None)
        or event.aggregate_type
    )
    subject_id = (
        state.get("subject_id")
        or payload.get("subject_id")
        or payload.get("incident_id")
        or payload.get("replay_case_id")
        or event.aggregate_id
    )
    return {
        "event_id": event.event_id,
        "event_type": event.event_name,
        "organization_id": str(
            state.get("organization_id")
            or payload.get("organization_id")
            or "unknown-org"
        ),
        "workspace_id": str(workspace_id),
        "subject_type": str(subject_type),
        "subject_id": str(subject_id),
        "occurred_at": _isoformat_utc(event.emitted_at),
        "payload": payload,
        "topic": _event_topic(event.event_name),
    }


def create_fastapi_app(service: OpsGraphAppService):
    ap = load_shared_agent_platform()
    try:
        from fastapi import FastAPI, Header, Request
        from fastapi.responses import JSONResponse, StreamingResponse
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

    @app.get("/health")
    def health(
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            HealthResponse(status="ok", product="opsgraph"),
            request_id=request_id,
        )

    @app.get("/api/v1/workflows")
    def list_workflows(
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.list_workflows(),
            request_id=request_id,
        )

    @app.get("/api/v1/workflows/{workflow_run_id}")
    def get_workflow_state(
        workflow_run_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_workflow_state(workflow_run_id),
            request_id=request_id,
        )

    @app.get("/api/v1/events/stream")
    async def stream_events(
        workspace_id: str,
        topic: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        runtime_stores = getattr(service, "runtime_stores", None)
        outbox_store = getattr(runtime_stores, "outbox_store", None)

        async def event_stream():
            seen_event_ids: set[str] = set()
            resume_after_id = last_event_id
            while True:
                emitted_any = False
                pending = outbox_store.list_pending() if outbox_store is not None else []
                resume_after_id = _normalize_resume_after_id(pending, resume_after_id)
                resume_matched = resume_after_id is None
                for stored in pending:
                    event = stored.event
                    if event.event_id in seen_event_ids:
                        continue
                    if not resume_matched:
                        if event.event_id == resume_after_id:
                            resume_matched = True
                        continue
                    context = _resolve_outbox_event_context(service, event)
                    if context is None:
                        continue
                    if str(context["workspace_id"]) != workspace_id:
                        continue
                    if not _matches_event_topic(context, topic):
                        continue
                    if subject_type is not None and str(context["subject_type"]) != subject_type:
                        continue
                    if subject_id is not None and str(context["subject_id"]) != subject_id:
                        continue
                    seen_event_ids.add(event.event_id)
                    emitted_any = True
                    yield _format_sse_message(
                        event_id=event.event_id,
                        event_name=event.event_name,
                        payload={key: value for key, value in context.items() if key != "topic"},
                    )
                resume_after_id = None
                if not emitted_any:
                    heartbeat_at = datetime.now(UTC)
                    heartbeat = {
                        "workspace_id": workspace_id,
                        "occurred_at": _isoformat_utc(heartbeat_at),
                    }
                    yield _format_sse_message(
                        event_id=f"heartbeat-{int(heartbeat_at.timestamp() * 1000)}",
                        event_name="heartbeat",
                        payload=heartbeat,
                    )
                await asyncio.sleep(15)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

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

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/hypotheses/{hypothesis_id}/decision")
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

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/recommendations/{recommendation_id}/decision")
    def decide_recommendation(
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.decide_recommendation(
                incident_id,
                recommendation_id,
                command,
                idempotency_key=idempotency_key,
            ),
            request_id=request_id,
        )

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

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/postmortem/finalize")
    def finalize_postmortem(
        incident_id: str,
        command: PostmortemFinalizeCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.finalize_postmortem(incident_id, command, idempotency_key=idempotency_key),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/postmortems")
    def list_postmortems(
        workspace_id: str,
        incident_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_postmortems(
            workspace_id,
            incident_id=incident_id,
            status=status,
        )
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
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

    @app.post("/api/v1/opsgraph/incidents/respond")
    def respond_to_incident(
        command: IncidentResponseCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.respond_to_incident(command),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/retrospective")
    def build_retrospective(
        command: RetrospectiveCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.build_retrospective(command),
            request_id=request_id,
        )

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

    @app.post("/api/v1/opsgraph/replays/baselines/capture")
    def capture_replay_baseline(
        command: ReplayBaselineCaptureCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.capture_replay_baseline(command),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/status")
    def update_replay_status(
        replay_run_id: str,
        command: ReplayStatusCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.update_replay_status(replay_run_id, command),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/execute")
    def execute_replay_run(
        replay_run_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.execute_replay_run(replay_run_id),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/evaluate")
    def evaluate_replay_run(
        replay_run_id: str,
        command: ReplayEvaluationCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.evaluate_replay_run(replay_run_id, command),
            request_id=request_id,
        )

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
