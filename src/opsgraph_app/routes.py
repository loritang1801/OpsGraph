from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from typing import Any

from .api_models import (
    ApprovalDecisionCommand,
    ApprovalDecisionResponse,
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
    RemoteProviderSmokeCommand,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    SeverityOverrideCommand,
)
from .auth import (
    CurrentUserResponse,
    HeaderOpsGraphAuthorizer,
    MembershipProvisionCommand,
    MembershipUpdateCommand,
    OpsGraphAuthorizationError,
    SessionCreateCommand,
)
from .route_events import resolve_outbox_event_context
from .route_replay_monitor import register_replay_monitor_routes
from .route_support import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    _event_topics,
    _event_topic,
    _format_sse_message,
    _isoformat_utc,
    _matches_event_topic,
    _normalize_resume_after_id,
    _replay_worker_status_event_id,
    _serialize_data,
    map_domain_error,
    paginate_collection,
    success_envelope,
)
from .service import OpsGraphAppService
from .shared_runtime import load_shared_agent_platform


def _resolve_outbox_event_context(service: OpsGraphAppService, event) -> dict[str, object] | None:
    return resolve_outbox_event_context(service, event)


def create_fastapi_app(service: OpsGraphAppService, *, route_authorizer=None):
    ap = load_shared_agent_platform()
    try:
        from fastapi import Cookie, Depends, FastAPI, Header, Query, Request, Response
        from fastapi.responses import JSONResponse, StreamingResponse
    except ImportError as exc:
        errors_module = importlib.import_module(f"{ap.__name__}.errors")
        FastAPIUnavailableError = errors_module.FastAPIUnavailableError

        raise FastAPIUnavailableError("fastapi is not installed") from exc

    app = FastAPI(title="OpsGraph API")
    ap.attach_service_lifecycle(app, service=service, state_attr="opsgraph_service")

    auth_service = getattr(service, "auth_service", None)
    route_authorizer = (
        route_authorizer
        or (auth_service.build_authorizer() if auth_service is not None else HeaderOpsGraphAuthorizer())
    )

    @app.exception_handler(KeyError)
    def handle_key_error(request: Request, exc: KeyError):
        status_code, payload = map_domain_error(exc, path=str(request.url.path))
        return JSONResponse(status_code=status_code, content=payload)

    @app.exception_handler(ValueError)
    def handle_value_error(request: Request, exc: ValueError):
        status_code, payload = map_domain_error(exc, path=str(request.url.path))
        return JSONResponse(status_code=status_code, content=payload)

    @app.exception_handler(OpsGraphAuthorizationError)
    def handle_authorization_error(request: Request, exc: OpsGraphAuthorizationError):
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    def _build_access_dependency(required_role: str):
        def require_access(
            authorization: str | None = Header(default=None, alias="Authorization"),
            organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
            user_id: str | None = Header(default=None, alias="X-User-Id"),
            user_role: str | None = Header(default=None, alias="X-User-Role"),
        ):
            return route_authorizer.authorize(
                required_role=required_role,
                authorization=authorization,
                organization_id=organization_id,
                user_id=user_id,
                user_role=user_role,
            )

        return require_access

    require_viewer_access = _build_access_dependency("viewer")
    require_operator_access = _build_access_dependency("operator")
    require_product_admin_access = _build_access_dependency("product_admin")

    if auth_service is not None:
        @app.post("/api/v1/auth/session")
        def create_auth_session(
            command: SessionCreateCommand,
            user_agent: str | None = Header(default=None, alias="User-Agent"),
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
        ) -> JSONResponse:
            issue = auth_service.create_session(
                command,
                ip_address=None,
                user_agent=user_agent,
            )
            response = JSONResponse(
                status_code=200,
                content=success_envelope(issue.response, request_id=request_id),
            )
            response.set_cookie(
                key="refresh_token",
                value=issue.refresh_token,
                httponly=True,
                samesite="lax",
                secure=False,
                path="/",
            )
            return response

        @app.post("/api/v1/auth/session/refresh")
        def refresh_auth_session(
            refresh_token: str | None = Cookie(default=None, alias="refresh_token"),
            user_agent: str | None = Header(default=None, alias="User-Agent"),
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
        ) -> JSONResponse:
            issue = auth_service.refresh_session(
                refresh_token,
                ip_address=None,
                user_agent=user_agent,
            )
            response = JSONResponse(
                status_code=200,
                content=success_envelope(issue.response, request_id=request_id),
            )
            response.set_cookie(
                key="refresh_token",
                value=issue.refresh_token,
                httponly=True,
                samesite="lax",
                secure=False,
                path="/",
            )
            return response

        @app.delete("/api/v1/auth/session/current", response_class=Response)
        def revoke_current_auth_session(
            auth_context=Depends(require_viewer_access),
        ) -> Response:
            auth_service.revoke_session(auth_context.session_id)
            response = Response(status_code=204)
            response.delete_cookie("refresh_token", path="/")
            return response

        @app.get("/api/v1/me")
        @app.get("/api/v1/auth/me")
        def get_current_user(
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
            auth_context=Depends(require_viewer_access),
        ) -> dict[str, object]:
            return success_envelope(
                auth_service.get_current_user(auth_context),
                request_id=request_id,
            )

        @app.get("/api/v1/auth/memberships")
        def list_auth_memberships(
            status: str | None = None,
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
            auth_context=Depends(require_product_admin_access),
        ) -> dict[str, object]:
            return success_envelope(
                auth_service.list_memberships(
                    auth_context.organization_id,
                    status=status,
                ),
                request_id=request_id,
            )

        @app.post("/api/v1/auth/memberships")
        def provision_auth_membership(
            command: MembershipProvisionCommand,
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
            auth_context=Depends(require_product_admin_access),
        ) -> dict[str, object]:
            return success_envelope(
                auth_service.provision_membership(
                    auth_context.organization_id,
                    command,
                    actor_user_id=auth_context.user_id,
                ),
                request_id=request_id,
            )

        @app.patch("/api/v1/auth/memberships/{membership_id}")
        def update_auth_membership(
            membership_id: str,
            command: MembershipUpdateCommand,
            request_id: str | None = Header(default=None, alias="X-Request-Id"),
            auth_context=Depends(require_product_admin_access),
        ) -> dict[str, object]:
            return success_envelope(
                auth_service.update_membership(
                    auth_context.organization_id,
                    membership_id,
                    command,
                    actor_user_id=auth_context.user_id,
                ),
                request_id=request_id,
            )

    @app.get("/health")
    def health(
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        health_factory = getattr(service, "get_health_status", None)
        health_payload = (
            health_factory()
            if callable(health_factory)
            else HealthResponse(status="ok", product="opsgraph")
        )
        return success_envelope(
            health_payload,
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/runtime-capabilities", dependencies=[Depends(require_product_admin_access)])
    def get_runtime_capabilities(
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_runtime_capabilities(),
            request_id=request_id,
        )

    @app.post(
        "/api/v1/opsgraph/runtime/remote-provider-smoke",
        dependencies=[Depends(require_product_admin_access)],
    )
    def run_remote_provider_smoke(
        command: RemoteProviderSmokeCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.run_remote_provider_smoke(
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get(
        "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
        dependencies=[Depends(require_product_admin_access)],
    )
    def list_remote_provider_smoke_runs(
        limit: int = 10,
        actor_user_id: str | None = None,
        smoke_request_id: str | None = Query(default=None, alias="request_id"),
        provider: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.list_remote_provider_smoke_runs(
                limit=limit,
                actor_user_id=actor_user_id,
                request_id=smoke_request_id,
                provider=provider,
            ),
            request_id=request_id,
        )

    @app.get(
        "/api/v1/opsgraph/runtime/remote-provider-smoke-summary",
        dependencies=[Depends(require_product_admin_access)],
    )
    def summarize_remote_provider_smoke_runs(
        limit: int = 50,
        actor_user_id: str | None = None,
        smoke_request_id: str | None = Query(default=None, alias="request_id"),
        provider: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.summarize_remote_provider_smoke_runs(
                limit=limit,
                actor_user_id=actor_user_id,
                request_id=smoke_request_id,
                provider=provider,
            ),
            request_id=request_id,
        )

    register_replay_monitor_routes(
        app,
        service,
        require_product_admin_access=require_product_admin_access,
    )

    @app.get("/api/v1/workflows", dependencies=[Depends(require_viewer_access)])
    def list_workflows(
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.list_workflows(),
            request_id=request_id,
        )

    @app.get("/api/v1/workflows/{workflow_run_id}", dependencies=[Depends(require_viewer_access)])
    def get_workflow_state(
        workflow_run_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_workflow_state(workflow_run_id),
            request_id=request_id,
        )

    @app.get("/api/v1/events/stream", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/incidents", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/incidents/{incident_id}", dependencies=[Depends(require_viewer_access)])
    def get_incident_workspace(
        incident_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_incident_workspace(incident_id),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/hypotheses", dependencies=[Depends(require_viewer_access)])
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
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.add_fact(
                incident_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/facts/{fact_id}/retract")
    def retract_fact(
        incident_id: str,
        fact_id: str,
        command: FactRetractCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.retract_fact(
                incident_id,
                fact_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/severity")
    def override_severity(
        incident_id: str,
        command: SeverityOverrideCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.override_severity(
                incident_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/hypotheses/{hypothesis_id}/decision")
    def decide_hypothesis(
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.decide_hypothesis(
                incident_id,
                hypothesis_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/recommendations", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/approval-tasks", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/audit-logs")
    def list_audit_logs(
        incident_id: str,
        action_type: str | None = None,
        actor_user_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        del auth_context
        items = service.list_audit_logs(
            incident_id,
            action_type=action_type,
            actor_user_id=actor_user_id,
        )
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/approval-tasks/{approval_task_id}", dependencies=[Depends(require_viewer_access)])
    def get_approval_task(
        approval_task_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_approval_task(approval_task_id),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/approvals/{approval_task_id}/decision")
    def decide_approval_task(
        approval_task_id: str,
        command: ApprovalDecisionCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.decide_approval_task(
                approval_task_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/recommendations/{recommendation_id}/decision")
    def decide_recommendation(
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.decide_recommendation(
                incident_id,
                recommendation_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/comms", dependencies=[Depends(require_viewer_access)])
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
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.publish_comms(
                incident_id,
                draft_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/resolve")
    def resolve_incident(
        incident_id: str,
        command: ResolveIncidentCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.resolve_incident(
                incident_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/{incident_id}/close")
    def close_incident(
        incident_id: str,
        command: CloseIncidentCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.close_incident(
                incident_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/incidents/{incident_id}/postmortem", dependencies=[Depends(require_viewer_access)])
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
        auth_context=Depends(require_operator_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.finalize_postmortem(
                incident_id,
                command,
                idempotency_key=idempotency_key,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/postmortems", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/replay-cases", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/replay-cases/{replay_case_id}", dependencies=[Depends(require_viewer_access)])
    def get_replay_case(
        replay_case_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_case(replay_case_id),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/respond", dependencies=[Depends(require_operator_access)])
    def respond_to_incident(
        command: IncidentResponseCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.respond_to_incident(command),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/incidents/retrospective", dependencies=[Depends(require_operator_access)])
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
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        response = service.start_replay_run(
            command,
            idempotency_key=idempotency_key,
            auth_context=auth_context,
            request_id=request_id,
        )
        return success_envelope(
            response,
            request_id=request_id,
            workflow_run_id=response.workflow_run_id,
        )

    @app.get("/api/v1/opsgraph/replays", dependencies=[Depends(require_viewer_access)])
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

    @app.get("/api/v1/opsgraph/replays/baselines", dependencies=[Depends(require_viewer_access)])
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
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.capture_replay_baseline(
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/process-queued")
    def process_queued_replays(
        workspace_id: str,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.process_queued_replays(
                workspace_id,
                limit=limit,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/status")
    def update_replay_status(
        replay_run_id: str,
        command: ReplayStatusCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.update_replay_status(
                replay_run_id,
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/execute")
    def execute_replay_run(
        replay_run_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.execute_replay_run(
                replay_run_id,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.post("/api/v1/opsgraph/replays/{replay_run_id}/evaluate")
    def evaluate_replay_run(
        replay_run_id: str,
        command: ReplayEvaluationCommand,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.evaluate_replay_run(
                replay_run_id,
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/summary", dependencies=[Depends(require_viewer_access)])
    def get_replay_quality_summary(
        workspace_id: str,
        incident_id: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_quality_summary(workspace_id, incident_id),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/reports", dependencies=[Depends(require_viewer_access)])
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
