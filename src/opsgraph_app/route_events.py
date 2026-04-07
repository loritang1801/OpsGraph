from __future__ import annotations

from typing import Any

from .route_support import _event_topic, _isoformat_utc
from .service import OpsGraphAppService


def resolve_outbox_event_context(service: OpsGraphAppService, event: Any) -> dict[str, object] | None:
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
