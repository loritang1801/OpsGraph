from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

ERROR_STATUS_BY_CODE = {
    "CONFLICT_STALE_RESOURCE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "FACT_VERSION_CONFLICT": 409,
    "HYPOTHESIS_STATUS_CONFLICT": 409,
    "RECOMMENDATION_STATUS_CONFLICT": 409,
    "APPROVAL_STATUS_CONFLICT": 409,
    "APPROVAL_REQUIRED": 409,
    "COMM_DRAFT_STALE_FACT_SET": 409,
    "COMM_DRAFT_ALREADY_PUBLISHED": 409,
    "INCIDENT_ALREADY_RESOLVED": 409,
    "INCIDENT_NOT_RESOLVED": 409,
    "APPROVAL_DECISION_INVALID": 422,
    "APPROVAL_EXECUTION_REQUIRES_RECOMMENDATION": 422,
    "APPROVAL_PUBLISH_FACT_SET_REQUIRED": 422,
    "APPROVAL_DRAFT_SELECTION_INVALID": 422,
    "REPLAY_RUN_NOT_EXECUTED": 409,
    "REPLAY_STATUS_CONFLICT": 409,
    "INVALID_REPLAY_BATCH_LIMIT": 400,
    "INVALID_REPLAY_WORKER_HISTORY_LIMIT": 400,
    "INVALID_REPLAY_WORKER_ALERT_WARNING_THRESHOLD": 400,
    "INVALID_REPLAY_WORKER_ALERT_CRITICAL_THRESHOLD": 400,
    "INVALID_REPLAY_MONITOR_PRESET_NAME": 400,
    "INVALID_REPLAY_MONITOR_PRESET_HISTORY_LIMIT": 400,
    "INVALID_REPLAY_MONITOR_PRESET_AUDIT_LIMIT": 400,
    "INVALID_REPLAY_MONITOR_PRESET_COPY_FORMAT": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_LABEL": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_START_TIME": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_END_TIME": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_WINDOW": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_TIMEZONE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_LABEL": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_OVERRIDE_DATE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_DUPLICATE_OVERRIDE_DATE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_RANGE_START_DATE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_RANGE_END_DATE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_RANGE": 400,
    "INVALID_REPLAY_MONITOR_SHIFT_OVERLAPPING_RANGE_OVERRIDE": 400,
    "INVALID_REMOTE_PROVIDER_SMOKE_HISTORY_LIMIT": 400,
    "ROOT_CAUSE_FACT_REQUIRED": 422,
    "REPLAY_EVALUATION_UNAVAILABLE": 503,
    "INVALID_CURSOR": 400,
}

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def map_domain_error(exc: Exception, *, path: str = "") -> tuple[int, dict[str, object]]:
    if isinstance(exc, KeyError):
        if "/incidents/" in path:
            code = "INCIDENT_NOT_FOUND"
        elif "/approval-tasks/" in path or "/approvals/" in path:
            code = "APPROVAL_TASK_NOT_FOUND"
        else:
            code = "RESOURCE_NOT_FOUND"
        resource_id = str(exc.args[0]) if exc.args else "resource"
        return 404, {"error": {"code": code, "message": f"{code}: {resource_id}"}}
    if isinstance(exc, ValueError):
        code = str(exc)
        status_code = ERROR_STATUS_BY_CODE.get(code, 400)
        return status_code, {"error": {"code": code, "message": code}}
    return 500, {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}


def _serialize_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    if isinstance(value, list):
        return [_serialize_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_data(item) for key, item in value.items()}
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


def paginate_collection(
    items: list[Any],
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> tuple[list[Any], str | None, bool]:
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


def _replay_worker_status_event_id(payload: dict[str, object]) -> str:
    workspace_id = str(payload.get("workspace_id") or "all")
    history = payload.get("history")
    if isinstance(history, list) and history:
        latest = history[0]
        if isinstance(latest, dict) and latest.get("emitted_at") is not None:
            return f"replay-worker:{workspace_id}:{latest['emitted_at']}"
    current = payload.get("current")
    if isinstance(current, dict) and current.get("last_seen_at") is not None:
        return f"replay-worker:{workspace_id}:{current['last_seen_at']}"
    return f"replay-worker:{workspace_id}:empty"
