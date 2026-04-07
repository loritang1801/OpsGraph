from __future__ import annotations

import asyncio
from datetime import datetime

from .api_models import (
    ReplayWorkerAlertPolicyUpdateCommand,
    ReplayWorkerMonitorPresetUpsertCommand,
    ReplayWorkerMonitorShiftScheduleUpdateCommand,
)
from .replay_worker_monitor_page import render_replay_worker_monitor_html
from .route_support import (
    DEFAULT_PAGE_LIMIT,
    _format_sse_message,
    _replay_worker_status_event_id,
    _serialize_data,
    paginate_collection,
    success_envelope,
)
from .service import OpsGraphAppService


def register_replay_monitor_routes(app, service: OpsGraphAppService, *, require_product_admin_access) -> None:
    from fastapi import Depends, Header, Query
    from fastapi.responses import HTMLResponse, StreamingResponse

    @app.get("/api/v1/opsgraph/replays/worker-alert-policy", dependencies=[Depends(require_product_admin_access)])
    def get_replay_worker_alert_policy(
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_worker_alert_policy(workspace_id),
            request_id=request_id,
        )

    @app.patch("/api/v1/opsgraph/replays/worker-alert-policy", dependencies=[Depends(require_product_admin_access)])
    def update_replay_worker_alert_policy(
        command: ReplayWorkerAlertPolicyUpdateCommand,
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.update_replay_worker_alert_policy(
                workspace_id,
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/worker-monitor-presets", dependencies=[Depends(require_product_admin_access)])
    def list_replay_worker_monitor_presets(
        workspace_id: str,
        shift_label: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.list_replay_worker_monitor_presets(workspace_id, shift_label=shift_label),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/worker-monitor-shift-schedule", dependencies=[Depends(require_product_admin_access)])
    def get_replay_worker_monitor_shift_schedule(
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_worker_monitor_shift_schedule(workspace_id),
            request_id=request_id,
        )

    @app.put("/api/v1/opsgraph/replays/worker-monitor-shift-schedule", dependencies=[Depends(require_product_admin_access)])
    def update_replay_worker_monitor_shift_schedule(
        command: ReplayWorkerMonitorShiftScheduleUpdateCommand,
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.update_replay_worker_monitor_shift_schedule(
                workspace_id,
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.delete("/api/v1/opsgraph/replays/worker-monitor-shift-schedule", dependencies=[Depends(require_product_admin_access)])
    def clear_replay_worker_monitor_shift_schedule(
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.clear_replay_worker_monitor_shift_schedule(
                workspace_id,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/worker-monitor-resolved-shift", dependencies=[Depends(require_product_admin_access)])
    def resolve_replay_worker_monitor_shift_label(
        workspace_id: str,
        at: datetime | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.resolve_replay_worker_monitor_shift_label(
                workspace_id,
                evaluated_at=at,
            ),
            request_id=request_id,
        )

    @app.put("/api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}", dependencies=[Depends(require_product_admin_access)])
    def upsert_replay_worker_monitor_preset(
        preset_name: str,
        command: ReplayWorkerMonitorPresetUpsertCommand,
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.upsert_replay_worker_monitor_preset(
                workspace_id,
                preset_name,
                command,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/worker-monitor-default-preset", dependencies=[Depends(require_product_admin_access)])
    def get_replay_worker_monitor_default_preset(
        workspace_id: str,
        shift_label: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_worker_monitor_default_preset(
                workspace_id,
                shift_label=shift_label,
            ),
            request_id=request_id,
        )

    @app.put("/api/v1/opsgraph/replays/worker-monitor-default-preset/{preset_name}", dependencies=[Depends(require_product_admin_access)])
    def set_replay_worker_monitor_default_preset(
        preset_name: str,
        workspace_id: str,
        shift_label: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.set_replay_worker_monitor_default_preset(
                workspace_id,
                preset_name,
                shift_label=shift_label,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.delete("/api/v1/opsgraph/replays/worker-monitor-default-preset", dependencies=[Depends(require_product_admin_access)])
    def clear_replay_worker_monitor_default_preset(
        workspace_id: str,
        shift_label: str | None = None,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.clear_replay_worker_monitor_default_preset(
                workspace_id,
                shift_label=shift_label,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.delete("/api/v1/opsgraph/replays/worker-monitor-presets/{preset_name}", dependencies=[Depends(require_product_admin_access)])
    def delete_replay_worker_monitor_preset(
        preset_name: str,
        workspace_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
        auth_context=Depends(require_product_admin_access),
    ) -> dict[str, object]:
        return success_envelope(
            service.delete_replay_worker_monitor_preset(
                workspace_id,
                preset_name,
                auth_context=auth_context,
                request_id=request_id,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/audit-logs", dependencies=[Depends(require_product_admin_access)])
    def list_replay_admin_audit_logs(
        workspace_id: str,
        action_type: str | None = None,
        actor_user_id: str | None = None,
        filter_request_id: str | None = Query(default=None, alias="request_id"),
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        items = service.list_replay_admin_audit_logs(
            workspace_id,
            action_type=action_type,
            actor_user_id=actor_user_id,
            request_id=filter_request_id,
        )
        page_items, next_cursor, has_more = paginate_collection(items, cursor=cursor, limit=limit)
        return success_envelope(
            page_items,
            request_id=request_id,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/v1/opsgraph/replays/worker-status", dependencies=[Depends(require_product_admin_access)])
    def get_replay_worker_status(
        workspace_id: str | None = None,
        history_limit: int = 10,
        request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> dict[str, object]:
        return success_envelope(
            service.get_replay_worker_status(
                workspace_id=workspace_id,
                history_limit=history_limit,
            ),
            request_id=request_id,
        )

    @app.get("/api/v1/opsgraph/replays/worker-status/stream", dependencies=[Depends(require_product_admin_access)])
    async def stream_replay_worker_status(
        workspace_id: str | None = None,
        history_limit: int = 10,
        once: bool = False,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        initial_status = service.get_replay_worker_status(
            workspace_id=workspace_id,
            history_limit=history_limit,
        )

        async def event_stream():
            current_snapshot = initial_status
            emitted_event_id = last_event_id
            while True:
                payload = _serialize_data(current_snapshot)
                event_id = _replay_worker_status_event_id(payload)
                if event_id != emitted_event_id:
                    yield _format_sse_message(
                        event_id=event_id,
                        event_name="opsgraph.replay_worker.status",
                        payload=payload,
                    )
                    emitted_event_id = event_id
                if once:
                    return
                await asyncio.sleep(1)
                current_snapshot = service.get_replay_worker_status(
                    workspace_id=workspace_id,
                    history_limit=history_limit,
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/opsgraph/replays/worker-monitor", dependencies=[Depends(require_product_admin_access)])
    def replay_worker_monitor_page() -> HTMLResponse:
        return HTMLResponse(render_replay_worker_monitor_html())
