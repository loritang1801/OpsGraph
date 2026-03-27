from __future__ import annotations

import asyncio
import base64
import binascii
import html
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
    "ROOT_CAUSE_FACT_REQUIRED": 422,
    "REPLAY_EVALUATION_UNAVAILABLE": 503,
    "INVALID_CURSOR": 400,
}

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
    ReplayWorkerAlertPolicyUpdateCommand,
    ReplayWorkerMonitorShiftScheduleUpdateCommand,
    ReplayWorkerMonitorPresetUpsertCommand,
    ReplayRunCommand,
    ReplayStatusCommand,
    ReplayRunSummary,
    ResolveIncidentCommand,
    RetrospectiveCommand,
    RuntimeCapabilitiesResponse,
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
from .service import OpsGraphAppService
from .shared_runtime import load_shared_agent_platform

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


def _render_replay_worker_monitor_html() -> str:
    title = html.escape("OpsGraph Replay Worker Monitor")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f4efe4;
      --panel: rgba(255, 252, 244, 0.86);
      --ink: #1f2a24;
      --muted: #5f6b63;
      --line: rgba(31, 42, 36, 0.14);
      --accent: #18654b;
      --accent-2: #b24c2d;
      --warn: #9c6b12;
      --ok: #1f7a55;
      --shadow: 0 20px 60px rgba(31, 42, 36, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Aptos, "Segoe UI Variable", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(24, 101, 75, 0.16), transparent 28rem),
        radial-gradient(circle at top right, rgba(178, 76, 45, 0.12), transparent 26rem),
        linear-gradient(180deg, #f8f4ea 0%, var(--bg) 100%);
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero {{
      display: grid;
      gap: 16px;
      grid-template-columns: 1.3fr 0.9fr;
      margin-bottom: 22px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    .hero-main {{
      padding: 28px;
    }}
    .eyebrow {{
      display: inline-flex;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(24, 101, 75, 0.1);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 14px 0 10px;
      font-size: clamp(34px, 6vw, 62px);
      line-height: 0.96;
      letter-spacing: -0.04em;
    }}
    .sub {{
      max-width: 48rem;
      margin: 0;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.6;
    }}
    .hero-side {{
      padding: 24px;
      display: grid;
      gap: 12px;
      align-content: start;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .metric-value {{
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 28px;
      font-weight: 700;
    }}
    .controls {{
      display: grid;
      gap: 14px;
      grid-template-columns: 1.2fr 0.6fr auto auto;
      align-items: end;
      padding: 18px;
      margin-bottom: 18px;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    input, select, button {{
      width: 100%;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
      padding: 12px 14px;
      font: inherit;
    }}
    button {{
      cursor: pointer;
      font-weight: 700;
      transition: transform 120ms ease, background 120ms ease;
    }}
    button.primary {{
      background: var(--accent);
      color: #f6f8f2;
      border-color: transparent;
    }}
    button:hover {{ transform: translateY(-1px); }}
    .status-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-bottom: 18px;
    }}
    .status-card {{
      padding: 18px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      background: rgba(31, 42, 36, 0.08);
    }}
    .pill.active {{ background: rgba(31, 122, 85, 0.12); color: var(--ok); }}
    .pill.idle {{ background: rgba(156, 107, 18, 0.12); color: var(--warn); }}
    .pill.retrying, .pill.failed, .pill.degraded {{ background: rgba(178, 76, 45, 0.12); color: var(--accent-2); }}
    .history {{
      overflow: hidden;
    }}
    .history-head {{
      padding: 18px 20px 8px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
    }}
    .history-head h2 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: -0.02em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 13px 14px;
      border-top: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    td.mono {{
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 13px;
    }}
    .foot {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }}
    .error {{
      margin-bottom: 14px;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(178, 76, 45, 0.12);
      color: var(--accent-2);
      display: none;
    }}
    .signal-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 18px;
    }}
    .signal-card h2 {{
      margin: 0 0 10px;
      font-size: 1.05rem;
    }}
    .signal-copy {{
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }}
    .alert-strip {{
      display: inline-flex;
      align-items: center;
      margin-bottom: 14px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 0.72rem;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      font-weight: 700;
      background: rgba(106, 167, 125, 0.14);
      color: var(--ok);
    }}
    .alert-strip.warning {{
      background: rgba(199, 154, 77, 0.18);
      color: var(--warn);
    }}
    .alert-strip.critical {{
      background: rgba(178, 76, 45, 0.16);
      color: var(--accent-2);
    }}
    .failure-meta {{
      display: grid;
      gap: 12px;
    }}
    .policy-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 14px;
    }}
    .editor-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 14px;
    }}
    .shift-editor-stack {{
      display: grid;
      gap: 14px;
      margin-bottom: 14px;
    }}
    .editor-panel {{
      padding: 14px;
      border-radius: 16px;
      background: rgba(15, 23, 42, 0.1);
      border: 1px solid rgba(148, 163, 184, 0.18);
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .section-head h3 {{
      margin: 0;
      font-size: 15px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .quick-form-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 12px;
      align-items: end;
    }}
    .quick-form-grid .wide {{
      grid-column: span 2;
    }}
    .shift-table-wrap {{
      overflow-x: auto;
      border-radius: 14px;
      border: 1px solid rgba(148, 163, 184, 0.14);
      background: rgba(255, 255, 255, 0.03);
      margin-bottom: 14px;
    }}
    .compact-table th, .compact-table td {{
      padding: 10px 12px;
    }}
    .compact-table td:last-child {{
      white-space: nowrap;
    }}
    .shift-table-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .inline-input {{
      width: 100%;
      margin-top: 8px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      background: rgba(15, 23, 42, 0.18);
      color: var(--text);
      font: inherit;
    }}
    .editor-textarea {{
      min-height: 160px;
      resize: vertical;
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
      line-height: 1.5;
    }}
    .policy-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 12px;
    }}
    .policy-feedback {{
      min-height: 1.5em;
      color: var(--muted);
    }}
    .policy-feedback.success {{
      color: var(--ok);
    }}
    .policy-feedback.error {{
      color: var(--accent-2);
    }}
    .audit-log-card {{
      margin-bottom: 18px;
    }}
    .policy-audit-fresh td {{
      background: rgba(199, 154, 77, 0.14);
    }}
    .fresh-flag {{
      display: inline-flex;
      align-items: center;
      margin-left: 8px;
      padding: 3px 7px;
      border-radius: 999px;
      background: rgba(31, 122, 85, 0.12);
      color: var(--ok);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .audit-row-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .audit-detail-row td {{
      background: rgba(15, 23, 42, 0.08);
    }}
    .audit-detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .audit-detail-panel {{
      padding: 14px;
      border-radius: 14px;
      background: rgba(15, 23, 42, 0.14);
      border: 1px solid rgba(148, 163, 184, 0.18);
    }}
    .audit-detail-panel h3 {{
      margin: 0 0 10px;
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .audit-json {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
    }}
    @media (max-width: 980px) {{
      .hero, .controls, .status-grid, .signal-grid, .policy-grid, .editor-grid, .quick-form-grid {{ grid-template-columns: 1fr; }}
      .audit-detail-grid {{ grid-template-columns: 1fr; }}
      .quick-form-grid .wide {{ grid-column: auto; }}
      .section-head {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <article class="card hero-main">
        <span class="eyebrow">Product Admin</span>
        <h1>{title}</h1>
        <p class="sub">Live view over the persisted replay worker heartbeat stream. This page subscribes to the same-origin SSE status feed used by automation and highlights the latest failure signal even when the worker process is separate from the API server.</p>
      </article>
      <aside class="card hero-side">
        <div>
          <div class="metric-label">Data Source</div>
          <div class="metric-value">/api/v1/opsgraph/replays/worker-status</div>
        </div>
        <div>
          <div class="metric-label">Refresh Mode</div>
        <div class="metric-value" id="refreshMode">Connecting live stream...</div>
        </div>
      </aside>
    </section>

    <section class="card controls">
      <label>
        Workspace
        <input id="workspaceId" name="workspace_id" placeholder="ops-ws-1">
      </label>
      <label>
        History
        <select id="historyLimit" name="history_limit">
          <option value="5">5 rows</option>
          <option value="10" selected>10 rows</option>
          <option value="20">20 rows</option>
        </select>
      </label>
      <button class="primary" id="refreshButton" type="button">Refresh Now</button>
      <button id="toggleAuto" type="button">Pause Live Stream</button>
    </section>

    <div class="error" id="errorBox"></div>

    <section class="signal-grid">
      <article class="card signal-card">
        <div class="alert-strip" id="alertLevel">No Alert</div>
        <h2 id="alertHeadline">Waiting for worker diagnostics...</h2>
        <p class="signal-copy" id="alertDetail">Open the worker or let the next heartbeat arrive to populate this panel.</p>
      </article>
      <article class="card signal-card">
        <h2>Latest Failure</h2>
        <div class="failure-meta">
          <div>
            <div class="metric-label">Status</div>
            <div class="metric-value" id="failureStatus">-</div>
          </div>
          <div>
            <div class="metric-label">Occurred At</div>
            <div class="metric-value" id="failureAt">-</div>
          </div>
          <div>
            <div class="metric-label">Message</div>
            <div class="signal-copy" id="failureMessage">No recent worker failure recorded.</div>
          </div>
        </div>
      </article>
    </section>

    <section class="status-grid">
      <article class="card status-card">
        <div class="metric-label">Worker Status</div>
        <div id="currentStatus" class="pill">No heartbeat</div>
      </article>
      <article class="card status-card">
        <div class="metric-label">Remaining Queued</div>
        <div class="metric-value" id="remainingQueued">-</div>
      </article>
      <article class="card status-card">
        <div class="metric-label">Last Seen</div>
        <div class="metric-value" id="lastSeen">-</div>
      </article>
      <article class="card status-card">
        <div class="metric-label">Consecutive Failures</div>
        <div class="metric-value" id="consecutiveFailures">0</div>
      </article>
    </section>

    <section class="card">
      <div class="history-head">
        <h2>Alert Policy</h2>
        <span class="foot" id="policyMeta">Runtime default thresholds are loading...</span>
      </div>
      <div class="policy-grid">
        <label>
          Warning Threshold
          <input id="warningThreshold" class="inline-input" type="number" min="1" step="1" value="1">
        </label>
        <label>
          Critical Threshold
          <input id="criticalThreshold" class="inline-input" type="number" min="1" step="1" value="3">
        </label>
        <div>
          <div class="metric-label">Policy Source</div>
          <div class="metric-value" id="policySource">default</div>
        </div>
        <div>
          <div class="metric-label">Updated At</div>
          <div class="metric-value" id="policyUpdatedAt">-</div>
        </div>
      </div>
      <p class="signal-copy" id="policyHint">This workspace is using the runtime default threshold pair.</p>
      <div class="policy-actions">
        <button class="primary" id="savePolicyButton" type="button">Save Policy</button>
        <button id="resetPolicyButton" type="button">Reset to Default</button>
        <span class="foot policy-feedback" id="policyStatus">Policy editor ready.</span>
      </div>
    </section>

    <section class="card">
      <div class="history-head">
        <h2>Shift Schedule</h2>
        <span class="foot" id="shiftScheduleMeta">Loading shift schedule...</span>
      </div>
      <div class="shift-editor-stack">
        <section class="editor-panel">
          <div class="section-head">
            <h3>Structured Editor</h3>
            <span class="foot" id="shiftScheduleDraftMeta">Loading structured draft...</span>
          </div>
          <p class="signal-copy">Use Edit to pull a row back into the quick form. Use Up or Down to change window order before saving.</p>
          <div class="quick-form-grid">
            <label>
              Base Window Label
              <input id="shiftScheduleBaseLabel" class="inline-input" type="text" placeholder="day">
            </label>
            <label>
              Base Start
              <input id="shiftScheduleBaseStart" class="inline-input" type="time">
            </label>
            <label>
              Base End
              <input id="shiftScheduleBaseEnd" class="inline-input" type="time">
            </label>
            <button id="addShiftScheduleBaseWindowButton" type="button">Add Base Window</button>
          </div>
          <div class="shift-table-wrap">
            <table class="compact-table">
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="shiftScheduleWindowsBody">
                <tr><td colspan="4" class="mono">No base windows configured.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="quick-form-grid">
            <label>
              Override Date
              <input id="shiftScheduleDateOverrideDate" class="inline-input" type="date">
            </label>
            <label class="wide">
              Date Note
              <input id="shiftScheduleDateOverrideNote" class="inline-input" type="text" placeholder="Holiday coverage">
            </label>
            <label>
              Window Label
              <input id="shiftScheduleDateOverrideLabel" class="inline-input" type="text" placeholder="holiday">
            </label>
            <label>
              Start
              <input id="shiftScheduleDateOverrideStart" class="inline-input" type="time">
            </label>
            <label>
              End
              <input id="shiftScheduleDateOverrideEnd" class="inline-input" type="time">
            </label>
            <button id="addShiftScheduleDateOverrideButton" type="button">Add Date Override Window</button>
          </div>
          <div class="shift-table-wrap">
            <table class="compact-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Note</th>
                  <th>Label</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="shiftScheduleDateOverridesBody">
                <tr><td colspan="6" class="mono">No date overrides configured.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="quick-form-grid">
            <label>
              Range Start
              <input id="shiftScheduleRangeOverrideStartDate" class="inline-input" type="date">
            </label>
            <label>
              Range End
              <input id="shiftScheduleRangeOverrideEndDate" class="inline-input" type="date">
            </label>
            <label class="wide">
              Range Note
              <input id="shiftScheduleRangeOverrideNote" class="inline-input" type="text" placeholder="Change freeze">
            </label>
            <label>
              Window Label
              <input id="shiftScheduleRangeOverrideLabel" class="inline-input" type="text" placeholder="freeze">
            </label>
            <label>
              Start
              <input id="shiftScheduleRangeOverrideStart" class="inline-input" type="time">
            </label>
            <label>
              End
              <input id="shiftScheduleRangeOverrideEnd" class="inline-input" type="time">
            </label>
            <button id="addShiftScheduleRangeOverrideButton" type="button">Add Range Override Window</button>
          </div>
          <div class="shift-table-wrap">
            <table class="compact-table">
              <thead>
                <tr>
                  <th>Start Date</th>
                  <th>End Date</th>
                  <th>Note</th>
                  <th>Label</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="shiftScheduleDateRangeOverridesBody">
                <tr><td colspan="7" class="mono">No range overrides configured.</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="editor-panel">
          <div class="section-head">
            <h3>Advanced JSON</h3>
            <span class="foot">Structured actions keep these arrays in sync. Edit JSON directly for bulk changes.</span>
          </div>
          <div class="editor-grid">
            <label>
              Timezone
              <input id="shiftScheduleTimezone" class="inline-input" type="text" placeholder="UTC">
            </label>
            <div class="signal-copy">
              Edit the workspace shift table used by auto shift resolution. Keep each editor as a JSON array.
            </div>
            <label>
              Base Windows JSON
              <textarea id="shiftScheduleWindows" class="inline-input editor-textarea" spellcheck="false" placeholder='[{{"shift_label":"day","start_time":"08:00","end_time":"20:00"}}]'></textarea>
            </label>
            <label>
              Date Overrides JSON
              <textarea id="shiftScheduleDateOverrides" class="inline-input editor-textarea" spellcheck="false" placeholder='[{{"date":"2026-12-25","note":"Holiday","windows":[{{"shift_label":"holiday","start_time":"10:00","end_time":"14:00"}}]}}]'></textarea>
            </label>
            <label>
              Range Overrides JSON
              <textarea id="shiftScheduleDateRangeOverrides" class="inline-input editor-textarea" spellcheck="false" placeholder='[{{"start_date":"2026-12-26","end_date":"2026-12-31","note":"Change freeze","windows":[{{"shift_label":"freeze","start_time":"09:00","end_time":"18:00"}}]}}]'></textarea>
            </label>
          </div>
        </section>
        <section class="editor-panel" id="shiftScheduleImportPreviewPanel" hidden>
          <div class="section-head">
            <h3>Import Preview</h3>
            <span class="foot" id="shiftScheduleImportPreviewMeta">No pending import preview.</span>
          </div>
          <p class="signal-copy" id="shiftScheduleImportPreviewText">Choose a shift schedule JSON file to compare it against the current draft before applying.</p>
          <div class="shift-table-wrap">
            <table class="compact-table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Current Draft</th>
                  <th>Imported</th>
                  <th>Delta</th>
                </tr>
              </thead>
              <tbody id="shiftScheduleImportPreviewBody">
                <tr><td colspan="4" class="mono">No import preview available.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="section-head">
            <h3>Detailed Window Diff</h3>
            <span class="foot">Added, removed, unchanged, and reordered entries are shown before draft replacement.</span>
          </div>
          <div class="shift-table-wrap">
            <table class="compact-table">
              <thead>
                <tr>
                  <th>Scope</th>
                  <th>Status</th>
                  <th>Entry</th>
                </tr>
              </thead>
              <tbody id="shiftScheduleImportDetailBody">
                <tr><td colspan="3" class="mono">No detailed import diff available.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="policy-actions">
            <button class="primary" id="applyShiftScheduleImportButton" type="button">Apply Import to Draft</button>
            <button id="discardShiftScheduleImportButton" type="button">Discard Import Preview</button>
          </div>
        </section>
      </div>
      <div class="policy-actions">
        <button id="loadShiftScheduleButton" type="button">Load Schedule</button>
        <button class="primary" id="saveShiftScheduleButton" type="button">Save Schedule</button>
        <button id="clearShiftScheduleButton" type="button">Clear Schedule</button>
        <button id="copyShiftScheduleJsonButton" type="button">Copy Schedule JSON</button>
        <button id="exportShiftScheduleJsonButton" type="button">Export Schedule JSON</button>
        <button id="importShiftScheduleJsonButton" type="button">Import Schedule JSON</button>
        <input id="shiftScheduleImportInput" type="file" accept="application/json,.json" hidden>
        <span class="foot policy-feedback" id="shiftScheduleActionStatus">Shift schedule editor ready.</span>
      </div>
    </section>

    <section class="card audit-log-card">
      <div class="history-head">
        <h2>Recent Policy Changes</h2>
        <span class="foot" id="policyAuditMeta">Waiting for audit data...</span>
      </div>
      <div class="policy-actions">
        <label>
          Actor User ID
          <input id="policyAuditActor" class="inline-input" type="text" placeholder="user-admin-1">
        </label>
        <label>
          Request ID
          <input id="policyAuditRequest" class="inline-input" type="text" placeholder="req-replay-policy-1">
        </label>
        <label>
          Audit Rows
          <select id="policyAuditLimit" class="inline-input">
            <option value="5">5</option>
            <option value="10">10</option>
            <option value="20">20</option>
            <option value="50">50</option>
          </select>
        </label>
        <button id="applyPolicyAuditFilters" type="button">Apply Filters</button>
        <button id="clearPolicyAuditFilters" type="button">Clear Filters</button>
        <button id="copyPolicyAuditLink" type="button">Copy Filter Link</button>
        <button id="copyLatestPolicyAuditContext" type="button">Copy Latest Context</button>
        <label>
          Copy Format
          <select id="policyAuditCopyFormat" class="inline-input">
            <option value="plain">Plain</option>
            <option value="markdown">Markdown</option>
            <option value="slack">Slack</option>
          </select>
        </label>
        <label>
          <input id="policyAuditIncludeSummary" type="checkbox" checked>
          Include Monitor Summary
        </label>
        <label>
          Preset Scope
          <select id="policyAuditPresetScope" class="inline-input">
            <option value="workspace">Workspace</option>
            <option value="browser">Browser</option>
          </select>
        </label>
        <label>
          Shift Source
          <select id="policyAuditShiftSource" class="inline-input">
            <option value="manual">Manual</option>
            <option value="auto">Auto</option>
          </select>
        </label>
        <label>
          Shift Label
          <input id="policyAuditShiftLabel" class="inline-input" type="text" placeholder="night">
        </label>
        <label>
          Preset Name
          <input id="policyAuditPresetName" class="inline-input" type="text" placeholder="night-shift">
        </label>
        <label>
          Saved Presets
          <select id="policyAuditPresetSelect" class="inline-input">
            <option value="">No saved presets</option>
          </select>
        </label>
        <button id="savePolicyAuditPreset" type="button">Save Preset</button>
        <button id="loadPolicyAuditPreset" type="button">Load Preset</button>
        <button id="deletePolicyAuditPreset" type="button">Delete Preset</button>
        <button id="setPolicyAuditDefaultPreset" type="button">Set Workspace Default</button>
        <button id="clearPolicyAuditDefaultPreset" type="button">Clear Default</button>
        <button id="exportPolicyAuditJson" type="button">Export JSON</button>
        <button id="exportPolicyAuditCsv" type="button">Export CSV</button>
        <button id="exportLatestPolicyAuditJson" type="button">Export Latest JSON</button>
        <button id="exportLatestPolicyAuditCsv" type="button">Export Latest CSV</button>
        <button id="loadOlderPolicyAudit" type="button">Load Older</button>
        <button id="resetPolicyAuditWindow" type="button">Newest First</button>
      </div>
      <p class="foot policy-feedback" id="policyAuditActionStatus">Audit tools ready.</p>
      <p class="foot" id="policyAuditShiftMeta">Manual shift label is active.</p>
      <table>
        <thead>
          <tr>
            <th>Recorded</th>
            <th>Actor</th>
            <th>Thresholds</th>
            <th>Result</th>
            <th>Request</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="policyAuditBody">
          <tr><td colspan="6" class="mono">No replay worker policy changes recorded.</td></tr>
        </tbody>
      </table>
    </section>

    <section class="card history">
      <div class="history-head">
        <h2>Recent Heartbeats</h2>
        <span class="foot" id="historyMeta">Waiting for data...</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Iteration</th>
            <th>Attempted</th>
            <th>Dispatched</th>
            <th>Failed</th>
            <th>Queued Left</th>
            <th>Emitted</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody id="historyBody">
          <tr><td colspan="8" class="mono">No heartbeat history available.</td></tr>
        </tbody>
      </table>
    </section>
  </main>

  <script>
    const workspaceInput = document.getElementById("workspaceId");
    const historyLimitInput = document.getElementById("historyLimit");
    const refreshButton = document.getElementById("refreshButton");
    const toggleAutoButton = document.getElementById("toggleAuto");
    const refreshMode = document.getElementById("refreshMode");
    const errorBox = document.getElementById("errorBox");
    const alertLevel = document.getElementById("alertLevel");
    const alertHeadline = document.getElementById("alertHeadline");
    const alertDetail = document.getElementById("alertDetail");
    const currentStatus = document.getElementById("currentStatus");
    const remainingQueued = document.getElementById("remainingQueued");
    const lastSeen = document.getElementById("lastSeen");
    const consecutiveFailures = document.getElementById("consecutiveFailures");
    const failureStatus = document.getElementById("failureStatus");
    const failureAt = document.getElementById("failureAt");
    const failureMessage = document.getElementById("failureMessage");
    const warningThresholdInput = document.getElementById("warningThreshold");
    const criticalThresholdInput = document.getElementById("criticalThreshold");
    const policySource = document.getElementById("policySource");
    const policyUpdatedAt = document.getElementById("policyUpdatedAt");
    const policyMeta = document.getElementById("policyMeta");
    const policyHint = document.getElementById("policyHint");
    const savePolicyButton = document.getElementById("savePolicyButton");
    const resetPolicyButton = document.getElementById("resetPolicyButton");
    const policyStatus = document.getElementById("policyStatus");
    const shiftScheduleTimezoneInput = document.getElementById("shiftScheduleTimezone");
    const shiftScheduleWindowsInput = document.getElementById("shiftScheduleWindows");
    const shiftScheduleDateOverridesInput = document.getElementById("shiftScheduleDateOverrides");
    const shiftScheduleDateRangeOverridesInput = document.getElementById("shiftScheduleDateRangeOverrides");
    const shiftScheduleDraftMeta = document.getElementById("shiftScheduleDraftMeta");
    const shiftScheduleBaseLabelInput = document.getElementById("shiftScheduleBaseLabel");
    const shiftScheduleBaseStartInput = document.getElementById("shiftScheduleBaseStart");
    const shiftScheduleBaseEndInput = document.getElementById("shiftScheduleBaseEnd");
    const addShiftScheduleBaseWindowButton = document.getElementById("addShiftScheduleBaseWindowButton");
    const shiftScheduleDateOverrideDateInput = document.getElementById("shiftScheduleDateOverrideDate");
    const shiftScheduleDateOverrideNoteInput = document.getElementById("shiftScheduleDateOverrideNote");
    const shiftScheduleDateOverrideLabelInput = document.getElementById("shiftScheduleDateOverrideLabel");
    const shiftScheduleDateOverrideStartInput = document.getElementById("shiftScheduleDateOverrideStart");
    const shiftScheduleDateOverrideEndInput = document.getElementById("shiftScheduleDateOverrideEnd");
    const addShiftScheduleDateOverrideButton = document.getElementById("addShiftScheduleDateOverrideButton");
    const shiftScheduleRangeOverrideStartDateInput = document.getElementById("shiftScheduleRangeOverrideStartDate");
    const shiftScheduleRangeOverrideEndDateInput = document.getElementById("shiftScheduleRangeOverrideEndDate");
    const shiftScheduleRangeOverrideNoteInput = document.getElementById("shiftScheduleRangeOverrideNote");
    const shiftScheduleRangeOverrideLabelInput = document.getElementById("shiftScheduleRangeOverrideLabel");
    const shiftScheduleRangeOverrideStartInput = document.getElementById("shiftScheduleRangeOverrideStart");
    const shiftScheduleRangeOverrideEndInput = document.getElementById("shiftScheduleRangeOverrideEnd");
    const addShiftScheduleRangeOverrideButton = document.getElementById("addShiftScheduleRangeOverrideButton");
    const shiftScheduleWindowsBody = document.getElementById("shiftScheduleWindowsBody");
    const shiftScheduleDateOverridesBody = document.getElementById("shiftScheduleDateOverridesBody");
    const shiftScheduleDateRangeOverridesBody = document.getElementById("shiftScheduleDateRangeOverridesBody");
    const loadShiftScheduleButton = document.getElementById("loadShiftScheduleButton");
    const saveShiftScheduleButton = document.getElementById("saveShiftScheduleButton");
    const clearShiftScheduleButton = document.getElementById("clearShiftScheduleButton");
    const copyShiftScheduleJsonButton = document.getElementById("copyShiftScheduleJsonButton");
    const exportShiftScheduleJsonButton = document.getElementById("exportShiftScheduleJsonButton");
    const importShiftScheduleJsonButton = document.getElementById("importShiftScheduleJsonButton");
    const shiftScheduleImportInput = document.getElementById("shiftScheduleImportInput");
    const shiftScheduleImportPreviewPanel = document.getElementById("shiftScheduleImportPreviewPanel");
    const shiftScheduleImportPreviewMeta = document.getElementById("shiftScheduleImportPreviewMeta");
    const shiftScheduleImportPreviewText = document.getElementById("shiftScheduleImportPreviewText");
    const shiftScheduleImportPreviewBody = document.getElementById("shiftScheduleImportPreviewBody");
    const shiftScheduleImportDetailBody = document.getElementById("shiftScheduleImportDetailBody");
    const applyShiftScheduleImportButton = document.getElementById("applyShiftScheduleImportButton");
    const discardShiftScheduleImportButton = document.getElementById("discardShiftScheduleImportButton");
    const shiftScheduleMeta = document.getElementById("shiftScheduleMeta");
    const shiftScheduleActionStatus = document.getElementById("shiftScheduleActionStatus");
    const policyAuditBody = document.getElementById("policyAuditBody");
    const policyAuditMeta = document.getElementById("policyAuditMeta");
    const policyAuditActorInput = document.getElementById("policyAuditActor");
    const policyAuditRequestInput = document.getElementById("policyAuditRequest");
    const policyAuditLimitInput = document.getElementById("policyAuditLimit");
    const applyPolicyAuditFiltersButton = document.getElementById("applyPolicyAuditFilters");
    const clearPolicyAuditFiltersButton = document.getElementById("clearPolicyAuditFilters");
    const copyPolicyAuditLinkButton = document.getElementById("copyPolicyAuditLink");
    const copyLatestPolicyAuditContextButton = document.getElementById("copyLatestPolicyAuditContext");
    const policyAuditCopyFormatInput = document.getElementById("policyAuditCopyFormat");
    const policyAuditIncludeSummaryInput = document.getElementById("policyAuditIncludeSummary");
    const policyAuditPresetScopeInput = document.getElementById("policyAuditPresetScope");
    const policyAuditShiftSourceInput = document.getElementById("policyAuditShiftSource");
    const policyAuditShiftLabelInput = document.getElementById("policyAuditShiftLabel");
    const policyAuditPresetNameInput = document.getElementById("policyAuditPresetName");
    const policyAuditPresetSelect = document.getElementById("policyAuditPresetSelect");
    const savePolicyAuditPresetButton = document.getElementById("savePolicyAuditPreset");
    const loadPolicyAuditPresetButton = document.getElementById("loadPolicyAuditPreset");
    const deletePolicyAuditPresetButton = document.getElementById("deletePolicyAuditPreset");
    const setPolicyAuditDefaultPresetButton = document.getElementById("setPolicyAuditDefaultPreset");
    const clearPolicyAuditDefaultPresetButton = document.getElementById("clearPolicyAuditDefaultPreset");
    const exportPolicyAuditJsonButton = document.getElementById("exportPolicyAuditJson");
    const exportPolicyAuditCsvButton = document.getElementById("exportPolicyAuditCsv");
    const exportLatestPolicyAuditJsonButton = document.getElementById("exportLatestPolicyAuditJson");
    const exportLatestPolicyAuditCsvButton = document.getElementById("exportLatestPolicyAuditCsv");
    const loadOlderPolicyAuditButton = document.getElementById("loadOlderPolicyAudit");
    const resetPolicyAuditWindowButton = document.getElementById("resetPolicyAuditWindow");
    const policyAuditActionStatus = document.getElementById("policyAuditActionStatus");
    const policyAuditShiftMeta = document.getElementById("policyAuditShiftMeta");
    const historyBody = document.getElementById("historyBody");
    const historyMeta = document.getElementById("historyMeta");
    const search = new URLSearchParams(window.location.search);
    workspaceInput.value = search.get("workspace_id") || "ops-ws-1";
    historyLimitInput.value = search.get("history_limit") || "10";
    policyAuditActorInput.value = search.get("actor_user_id") || "";
    policyAuditRequestInput.value = search.get("request_id") || "";
    policyAuditLimitInput.value = ["5", "10", "20", "50"].includes(search.get("policy_audit_limit") || "")
      ? search.get("policy_audit_limit")
      : "5";
    policyAuditCopyFormatInput.value = ["plain", "markdown", "slack"].includes(search.get("policy_audit_copy_format") || "")
      ? search.get("policy_audit_copy_format")
      : "plain";
    policyAuditIncludeSummaryInput.checked = search.get("policy_audit_include_summary") !== "0";
    policyAuditPresetScopeInput.value = ["workspace", "browser"].includes(search.get("policy_audit_preset_scope") || "")
      ? search.get("policy_audit_preset_scope")
      : "workspace";
    policyAuditShiftSourceInput.value = ["manual", "auto"].includes(search.get("policy_audit_shift_source") || "")
      ? search.get("policy_audit_shift_source")
      : "manual";
    policyAuditShiftLabelInput.value = search.get("policy_audit_shift_label") || "";
    policyAuditPresetNameInput.value = search.get("policy_audit_preset_name") || "";

    const policyAuditPresetStorageKey = "opsgraph.replay_worker_monitor_presets.v1";
    let liveSource = null;
    let liveUpdates = true;
    let currentPolicy = null;
    let currentShiftSchedule = null;
    let pendingImportedShiftSchedule = null;
    let policyAuditCursor = null;
    let policyAuditHasMore = false;
    let policyAuditItems = [];
    let policyAuditWindowExpanded = false;
    let policyAuditSeenIds = new Set();
    let policyAuditFreshIds = new Set();
    let policyAuditExpandedIds = new Set();
    let policyAuditBrowserPresets = {{}};
    let policyAuditWorkspacePresets = {{}};
    let policyAuditPresets = {{}};
    let currentResolvedPolicyAuditShift = null;
    let currentWorkerStatusData = null;

    function applyStatusPill(element, value) {{
      const normalized = (value || "unknown").toLowerCase();
      element.className = "pill " + normalized;
      element.textContent = value || "No heartbeat";
    }}

    function applyAlert(alert) {{
      const level = String(alert?.level || "healthy").toLowerCase();
      alertLevel.className = "alert-strip " + level;
      if (!alert) {{
        alertLevel.textContent = "No Alert";
        alertHeadline.textContent = "Waiting for worker diagnostics...";
        alertDetail.textContent = "Open the worker or let the next heartbeat arrive to populate this panel.";
        failureStatus.textContent = "-";
        failureAt.textContent = "-";
        failureMessage.textContent = "No recent worker failure recorded.";
        return;
      }}
      alertLevel.textContent = String(alert.level || "healthy").toUpperCase();
      alertHeadline.textContent = alert.headline || "Replay worker status";
      alertDetail.textContent = alert.detail || "No additional alert detail available.";
      failureStatus.textContent = alert.latest_failure_status || "-";
      failureAt.textContent = alert.latest_failure_at || "-";
      failureMessage.textContent = alert.latest_failure_message || "No recent worker failure recorded.";
    }}

    function setPolicyStatus(message, tone = "neutral") {{
      policyStatus.className = "foot policy-feedback";
      if (tone === "success" || tone === "error") {{
        policyStatus.className += " " + tone;
      }}
      policyStatus.textContent = message;
    }}

    function setShiftScheduleStatus(message, tone = "neutral") {{
      shiftScheduleActionStatus.className = "foot policy-feedback";
      if (tone === "success" || tone === "error") {{
        shiftScheduleActionStatus.className += " " + tone;
      }}
      shiftScheduleActionStatus.textContent = message;
    }}

    function setPolicyAuditActionStatus(message, tone = "neutral") {{
      policyAuditActionStatus.className = "foot policy-feedback";
      if (tone === "success" || tone === "error") {{
        policyAuditActionStatus.className += " " + tone;
      }}
      policyAuditActionStatus.textContent = message;
    }}

    function formatJsonEditorValue(value) {{
      return JSON.stringify(value || [], null, 2);
    }}

    function parseJsonArrayEditorValue(rawValue, label) {{
      const normalized = String(rawValue || "").trim();
      if (!normalized) {{
        return [];
      }}
      let parsed;
      try {{
        parsed = JSON.parse(normalized);
      }} catch (error) {{
        throw new Error(`${{label}} must be valid JSON.`);
      }}
      if (!Array.isArray(parsed)) {{
        throw new Error(`${{label}} must be a JSON array.`);
      }}
      return parsed;
    }}

    function getPolicyAuditCopyFormat() {{
      return ["plain", "markdown", "slack"].includes(policyAuditCopyFormatInput.value)
        ? policyAuditCopyFormatInput.value
        : "plain";
    }}

    function getPolicyAuditIncludeSummary() {{
      return policyAuditIncludeSummaryInput.checked;
    }}

    function getPolicyAuditPresetScope() {{
      return ["workspace", "browser"].includes(policyAuditPresetScopeInput.value)
        ? policyAuditPresetScopeInput.value
        : "workspace";
    }}

    function getPolicyAuditShiftSource() {{
      return ["manual", "auto"].includes(policyAuditShiftSourceInput.value)
        ? policyAuditShiftSourceInput.value
        : "manual";
    }}

    function getPolicyAuditShiftLabel() {{
      return String(policyAuditShiftLabelInput.value || "").trim();
    }}

    function getEffectivePolicyAuditShiftLabel() {{
      if (getPolicyAuditShiftSource() === "auto") {{
        return String(currentResolvedPolicyAuditShift?.shift_label || "").trim();
      }}
      return getPolicyAuditShiftLabel();
    }}

    function getPolicyAuditDefaultSource() {{
      return getEffectivePolicyAuditShiftLabel() ? "shift_default" : "workspace_default";
    }}

    function getPolicyAuditDefaultScopeLabel() {{
      const shiftLabel = getEffectivePolicyAuditShiftLabel();
      return shiftLabel ? `shift default (${{
        shiftLabel
      }})` : "workspace default";
    }}

    function normalizePolicyAuditPresetName(value) {{
      return String(value || "").trim().replace(/\\s+/g, " ");
    }}

    function buildPolicyAuditPresetSnapshot() {{
      return {{
        workspace_id: workspaceInput.value.trim() || "ops-ws-1",
        history_limit: historyLimitInput.value || "10",
        actor_user_id: policyAuditActorInput.value.trim() || "",
        request_id: policyAuditRequestInput.value.trim() || "",
        policy_audit_limit: policyAuditLimitInput.value || "5",
        policy_audit_copy_format: getPolicyAuditCopyFormat(),
        policy_audit_include_summary: getPolicyAuditIncludeSummary(),
      }};
    }}

    function getDefaultWorkspacePolicyAuditPresetName() {{
      return Object.entries(policyAuditWorkspacePresets).find(([, snapshot]) => snapshot.is_default)?.[0] || "";
    }}

    function getScopedWorkspacePolicyAuditDefaultPresetName() {{
      const desiredSource = getPolicyAuditDefaultSource();
      return (
        Object.entries(policyAuditWorkspacePresets).find(
          ([, snapshot]) => snapshot.default_source === desiredSource,
        )?.[0] || ""
      );
    }}

    function getCurrentMonitorSearch() {{
      return new URLSearchParams(window.location.search);
    }}

    function renderPolicyAuditShiftMeta() {{
      if (getPolicyAuditShiftSource() !== "auto") {{
        const manualShiftLabel = getPolicyAuditShiftLabel();
        policyAuditShiftLabelInput.disabled = false;
        policyAuditShiftMeta.textContent = manualShiftLabel
          ? `Manual shift label is active: ${{manualShiftLabel}}.`
          : "Manual shift label is active.";
        return;
      }}
      policyAuditShiftLabelInput.disabled = true;
      if (!workspaceInput.value.trim()) {{
        policyAuditShiftMeta.textContent = "Auto shift resolution is waiting for a workspace.";
        return;
      }}
      if (!currentResolvedPolicyAuditShift) {{
        policyAuditShiftMeta.textContent = "Auto shift resolution is loading current workspace schedule...";
        return;
      }}
      if (currentResolvedPolicyAuditShift.source === "date_range_override" && currentResolvedPolicyAuditShift.shift_label) {{
        policyAuditShiftMeta.textContent = `Auto shift resolved to ${{
          currentResolvedPolicyAuditShift.shift_label
        }} via range override ${{
          currentResolvedPolicyAuditShift.override_range_start_date || "-"
        }} to ${{
          currentResolvedPolicyAuditShift.override_range_end_date || "-"
        }}${{
          currentResolvedPolicyAuditShift.override_note
            ? ` (${{currentResolvedPolicyAuditShift.override_note}})`
            : ""
        }}.`;
        return;
      }}
      if (currentResolvedPolicyAuditShift.source === "date_range_override") {{
        policyAuditShiftMeta.textContent = `Auto shift is suppressed by range override ${{
          currentResolvedPolicyAuditShift.override_range_start_date || "-"
        }} to ${{
          currentResolvedPolicyAuditShift.override_range_end_date || "-"
        }}${{
          currentResolvedPolicyAuditShift.override_note
            ? ` (${{currentResolvedPolicyAuditShift.override_note}})`
            : ""
        }} and will not fall back to the base schedule.`;
        return;
      }}
      if (currentResolvedPolicyAuditShift.source === "date_override" && currentResolvedPolicyAuditShift.shift_label) {{
        policyAuditShiftMeta.textContent = `Auto shift resolved to ${{
          currentResolvedPolicyAuditShift.shift_label
        }} via date override ${{
          currentResolvedPolicyAuditShift.override_date || "-"
        }}${{
          currentResolvedPolicyAuditShift.override_note
            ? ` (${{currentResolvedPolicyAuditShift.override_note}})`
            : ""
        }}.`;
        return;
      }}
      if (currentResolvedPolicyAuditShift.source === "date_override") {{
        policyAuditShiftMeta.textContent = `Auto shift is suppressed by date override ${{
          currentResolvedPolicyAuditShift.override_date || "-"
        }}${{
          currentResolvedPolicyAuditShift.override_note
            ? ` (${{currentResolvedPolicyAuditShift.override_note}})`
            : ""
        }} and will not fall back to the base schedule.`;
        return;
      }}
      if (currentResolvedPolicyAuditShift.shift_label) {{
        policyAuditShiftMeta.textContent = `Auto shift resolved to ${{
          currentResolvedPolicyAuditShift.shift_label
        }} via ${{
          currentResolvedPolicyAuditShift.timezone || "UTC"
        }}.`;
        return;
      }}
      policyAuditShiftMeta.textContent = `Auto shift found no active window and will fall back to the workspace default.${{
        currentResolvedPolicyAuditShift.timezone
          ? ` Timezone: ${{currentResolvedPolicyAuditShift.timezone}}.`
          : ""
      }}`;
    }}

    function hasExplicitPolicyAuditSelectionInQuery() {{
      const currentSearch = getCurrentMonitorSearch();
      return [
        "actor_user_id",
        "request_id",
        "policy_audit_limit",
        "policy_audit_copy_format",
        "policy_audit_include_summary",
        "history_limit",
      ].some((key) => currentSearch.has(key));
    }}

    function syncActivePolicyAuditPresets() {{
      policyAuditPresets = getPolicyAuditPresetScope() === "workspace"
        ? policyAuditWorkspacePresets
        : policyAuditBrowserPresets;
    }}

    function getSelectedPolicyAuditPresetName() {{
      syncActivePolicyAuditPresets();
      const selectedName = normalizePolicyAuditPresetName(policyAuditPresetSelect.value);
      if (selectedName && policyAuditPresets[selectedName]) {{
        return selectedName;
      }}
      const typedName = normalizePolicyAuditPresetName(policyAuditPresetNameInput.value);
      if (typedName && policyAuditPresets[typedName]) {{
        return typedName;
      }}
      return "";
    }}

    function loadStoredBrowserPolicyAuditPresets() {{
      try {{
        const rawValue = window.localStorage.getItem(policyAuditPresetStorageKey);
        if (!rawValue) {{
          return {{}};
        }}
        const parsed = JSON.parse(rawValue);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {{
          return {{}};
        }}
        const nextPresets = {{}};
        Object.entries(parsed).forEach(([presetName, snapshot]) => {{
          const normalizedName = normalizePolicyAuditPresetName(presetName);
          if (!normalizedName || !snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {{
            return;
          }}
          nextPresets[normalizedName] = {{
            workspace_id: String(snapshot.workspace_id || "ops-ws-1"),
            history_limit: String(snapshot.history_limit || "10"),
            actor_user_id: String(snapshot.actor_user_id || ""),
            request_id: String(snapshot.request_id || ""),
            policy_audit_limit: String(snapshot.policy_audit_limit || "5"),
            policy_audit_copy_format: String(snapshot.policy_audit_copy_format || "plain"),
            policy_audit_include_summary: snapshot.policy_audit_include_summary !== false,
            is_default: false,
          }};
        }});
        return nextPresets;
      }} catch (_error) {{
        return {{}};
      }}
    }}

    function persistBrowserPolicyAuditPresets() {{
      try {{
        if (Object.keys(policyAuditBrowserPresets).length === 0) {{
          window.localStorage.removeItem(policyAuditPresetStorageKey);
        }} else {{
          window.localStorage.setItem(policyAuditPresetStorageKey, JSON.stringify(policyAuditBrowserPresets));
        }}
        return true;
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus("Preset storage is unavailable in this browser.", "error");
        return false;
      }}
    }}

    async function fetchWorkspacePolicyAuditPresets() {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        return {{}};
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const shiftLabel = getEffectivePolicyAuditShiftLabel();
      if (shiftLabel) {{
        params.set("shift_label", shiftLabel);
      }}
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-presets?${{params.toString()}}`, {{
        headers: {{ "Accept": "application/json" }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      const nextPresets = {{}};
      (Array.isArray(payload.data) ? payload.data : []).forEach((item) => {{
        const presetName = normalizePolicyAuditPresetName(item.preset_name || "");
        if (!presetName) {{
          return;
        }}
        nextPresets[presetName] = {{
          workspace_id: String(item.workspace_id || workspaceId),
          history_limit: String(item.history_limit || "10"),
          actor_user_id: String(item.actor_user_id || ""),
          request_id: String(item.request_id || ""),
          policy_audit_limit: String(item.policy_audit_limit || "5"),
          policy_audit_copy_format: String(item.policy_audit_copy_format || "plain"),
          policy_audit_include_summary: item.policy_audit_include_summary !== false,
          is_default: item.is_default === true,
          default_source: String(item.default_source || "none"),
          updated_at: item.updated_at || null,
        }};
      }});
      return nextPresets;
    }}

    async function refreshPolicyAuditShiftResolution() {{
      currentResolvedPolicyAuditShift = null;
      renderPolicyAuditShiftMeta();
      if (getPolicyAuditShiftSource() !== "auto") {{
        return null;
      }}
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        return null;
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-resolved-shift?${{params.toString()}}`, {{
        headers: {{ "Accept": "application/json" }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      currentResolvedPolicyAuditShift = payload.data || null;
      renderPolicyAuditShiftMeta();
      return currentResolvedPolicyAuditShift;
    }}

    function buildShiftScheduleDraft({{ preserveUpdatedAt = true }} = {{}}) {{
      const command = buildShiftScheduleCommand();
      return {{
        workspace_id: currentShiftSchedule?.workspace_id || workspaceInput.value.trim() || null,
        timezone: command.timezone || "UTC",
        windows: command.windows,
        date_overrides: command.date_overrides,
        date_range_overrides: command.date_range_overrides,
        updated_at: preserveUpdatedAt ? (currentShiftSchedule?.updated_at || null) : null,
      }};
    }}

    function renderShiftScheduleEmptyTable(body, columnCount, message) {{
      body.innerHTML = `<tr><td colspan="${{columnCount}}" class="mono">${{escapeHtml(message)}}</td></tr>`;
    }}

    function renderShiftScheduleDraftParseError(message) {{
      shiftScheduleDraftMeta.textContent = `Structured editor is waiting for valid JSON: ${{message}}`;
      renderShiftScheduleEmptyTable(shiftScheduleWindowsBody, 4, message);
      renderShiftScheduleEmptyTable(shiftScheduleDateOverridesBody, 6, message);
      renderShiftScheduleEmptyTable(shiftScheduleDateRangeOverridesBody, 7, message);
    }}

    function renderShiftScheduleStructuredTables(schedule) {{
      const baseWindows = Array.isArray(schedule?.windows) ? schedule.windows : [];
      const dateOverrides = Array.isArray(schedule?.date_overrides) ? schedule.date_overrides : [];
      const dateRangeOverrides = Array.isArray(schedule?.date_range_overrides) ? schedule.date_range_overrides : [];
      const dateWindowCount = dateOverrides.reduce(
        (total, item) => total + (Array.isArray(item?.windows) ? item.windows.length : 0),
        0,
      );
      const rangeWindowCount = dateRangeOverrides.reduce(
        (total, item) => total + (Array.isArray(item?.windows) ? item.windows.length : 0),
        0,
      );
      shiftScheduleDraftMeta.textContent = [
        `base=${{baseWindows.length}}`,
        `date-groups=${{dateOverrides.length}}`,
        `date-windows=${{dateWindowCount}}`,
        `range-groups=${{dateRangeOverrides.length}}`,
        `range-windows=${{rangeWindowCount}}`,
      ].join(" | ");
      if (baseWindows.length === 0) {{
        renderShiftScheduleEmptyTable(shiftScheduleWindowsBody, 4, "No base windows configured.");
      }} else {{
        shiftScheduleWindowsBody.innerHTML = baseWindows.map((item, index) => `
          <tr>
            <td class="mono">${{escapeHtml(String(item?.shift_label || "-"))}}</td>
            <td class="mono">${{escapeHtml(String(item?.start_time || "-"))}}</td>
            <td class="mono">${{escapeHtml(String(item?.end_time || "-"))}}</td>
            <td>
              <div class="shift-table-actions">
                <button type="button" data-shift-schedule-action="edit-base-window" data-window-index="${{index}}">Edit</button>
                <button type="button" data-shift-schedule-action="move-base-window-up" data-window-index="${{index}}" ${{index === 0 ? "disabled" : ""}}>Up</button>
                <button type="button" data-shift-schedule-action="move-base-window-down" data-window-index="${{index}}" ${{index === baseWindows.length - 1 ? "disabled" : ""}}>Down</button>
                <button type="button" data-shift-schedule-action="remove-base-window" data-window-index="${{index}}">Remove</button>
              </div>
            </td>
          </tr>
        `).join("");
      }}
      if (dateOverrides.length === 0 || dateWindowCount === 0) {{
        renderShiftScheduleEmptyTable(shiftScheduleDateOverridesBody, 6, "No date overrides configured.");
      }} else {{
        shiftScheduleDateOverridesBody.innerHTML = dateOverrides.flatMap((item, overrideIndex) => {{
          const windows = Array.isArray(item?.windows) ? item.windows : [];
          return windows.map((windowItem, windowIndex) => `
            <tr>
              <td class="mono">${{escapeHtml(String(item?.date || "-"))}}</td>
              <td>${{escapeHtml(String(item?.note || ""))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.shift_label || "-"))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.start_time || "-"))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.end_time || "-"))}}</td>
              <td>
                <div class="shift-table-actions">
                  <button type="button" data-shift-schedule-action="edit-date-override-window" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}">Edit</button>
                  <button type="button" data-shift-schedule-action="move-date-override-window-up" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}" ${{windowIndex === 0 ? "disabled" : ""}}>Up</button>
                  <button type="button" data-shift-schedule-action="move-date-override-window-down" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}" ${{windowIndex === windows.length - 1 ? "disabled" : ""}}>Down</button>
                  <button type="button" data-shift-schedule-action="remove-date-override-window" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}">Remove</button>
                </div>
              </td>
            </tr>
          `);
        }}).join("");
      }}
      if (dateRangeOverrides.length === 0 || rangeWindowCount === 0) {{
        renderShiftScheduleEmptyTable(shiftScheduleDateRangeOverridesBody, 7, "No range overrides configured.");
      }} else {{
        shiftScheduleDateRangeOverridesBody.innerHTML = dateRangeOverrides.flatMap((item, overrideIndex) => {{
          const windows = Array.isArray(item?.windows) ? item.windows : [];
          return windows.map((windowItem, windowIndex) => `
            <tr>
              <td class="mono">${{escapeHtml(String(item?.start_date || "-"))}}</td>
              <td class="mono">${{escapeHtml(String(item?.end_date || "-"))}}</td>
              <td>${{escapeHtml(String(item?.note || ""))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.shift_label || "-"))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.start_time || "-"))}}</td>
              <td class="mono">${{escapeHtml(String(windowItem?.end_time || "-"))}}</td>
              <td>
                <div class="shift-table-actions">
                  <button type="button" data-shift-schedule-action="edit-range-override-window" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}">Edit</button>
                  <button type="button" data-shift-schedule-action="move-range-override-window-up" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}" ${{windowIndex === 0 ? "disabled" : ""}}>Up</button>
                  <button type="button" data-shift-schedule-action="move-range-override-window-down" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}" ${{windowIndex === windows.length - 1 ? "disabled" : ""}}>Down</button>
                  <button type="button" data-shift-schedule-action="remove-range-override-window" data-override-index="${{overrideIndex}}" data-window-index="${{windowIndex}}">Remove</button>
                </div>
              </td>
            </tr>
          `);
        }}).join("");
      }}
    }}

    function moveArrayItem(items, fromIndex, toIndex) {{
      if (!Array.isArray(items)) {{
        return [];
      }}
      if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= items.length || toIndex >= items.length) {{
        return items.slice();
      }}
      const nextItems = items.slice();
      const [movedItem] = nextItems.splice(fromIndex, 1);
      nextItems.splice(toIndex, 0, movedItem);
      return nextItems;
    }}

    function normalizeImportedShiftSchedule(rawValue) {{
      const candidate = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) && rawValue.data
        ? rawValue.data
        : rawValue;
      if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {{
        throw new Error("Imported shift schedule must be a JSON object.");
      }}
      const windows = candidate.windows;
      const dateOverrides = candidate.date_overrides;
      const dateRangeOverrides = candidate.date_range_overrides;
      if (windows !== undefined && !Array.isArray(windows)) {{
        throw new Error("Imported shift schedule windows must be a JSON array.");
      }}
      if (dateOverrides !== undefined && !Array.isArray(dateOverrides)) {{
        throw new Error("Imported shift schedule date_overrides must be a JSON array.");
      }}
      if (dateRangeOverrides !== undefined && !Array.isArray(dateRangeOverrides)) {{
        throw new Error("Imported shift schedule date_range_overrides must be a JSON array.");
      }}
      return {{
        workspace_id: workspaceInput.value.trim() || candidate.workspace_id || null,
        timezone: String(candidate.timezone || "UTC"),
        windows: Array.isArray(windows) ? windows : [],
        date_overrides: Array.isArray(dateOverrides) ? dateOverrides : [],
        date_range_overrides: Array.isArray(dateRangeOverrides) ? dateRangeOverrides : [],
        updated_at: null,
      }};
    }}

    function buildShiftScheduleExportFilename(schedule) {{
      const workspacePart = sanitizeFilePart(schedule?.workspace_id || workspaceInput.value.trim(), "workspace");
      const timezonePart = sanitizeFilePart(schedule?.timezone || "UTC", "utc");
      const updatedPart = sanitizeFilePart(schedule?.updated_at || "draft", "draft");
      return `${{workspacePart}}-shift-schedule-${{timezonePart}}-${{updatedPart}}`;
    }}

    function countShiftScheduleNestedWindows(items) {{
      if (!Array.isArray(items)) {{
        return 0;
      }}
      return items.reduce(
        (total, item) => total + (Array.isArray(item?.windows) ? item.windows.length : 0),
        0,
      );
    }}

    function buildShiftScheduleComparisonEntries(schedule) {{
      const entries = [];
      const baseWindows = Array.isArray(schedule?.windows) ? schedule.windows : [];
      baseWindows.forEach((item) => {{
        entries.push({{
          scope: "Base Window",
          key: `base:${{item?.shift_label || ""}}|${{item?.start_time || ""}}|${{item?.end_time || ""}}`,
          label: `${{item?.shift_label || "-"}} ${{item?.start_time || "-"}}-${{item?.end_time || "-"}}`,
        }});
      }});
      const dateOverrides = Array.isArray(schedule?.date_overrides) ? schedule.date_overrides : [];
      dateOverrides.forEach((item) => {{
        const windows = Array.isArray(item?.windows) ? item.windows : [];
        windows.forEach((windowItem) => {{
          const note = String(item?.note || "").trim();
          entries.push({{
            scope: "Date Override Window",
            key: `date:${{item?.date || ""}}|${{note}}|${{windowItem?.shift_label || ""}}|${{windowItem?.start_time || ""}}|${{windowItem?.end_time || ""}}`,
            label: `${{item?.date || "-"}}${{note ? ` (${{note}})` : ""}} | ${{windowItem?.shift_label || "-"}} ${{windowItem?.start_time || "-"}}-${{windowItem?.end_time || "-"}}`,
          }});
        }});
      }});
      const dateRangeOverrides = Array.isArray(schedule?.date_range_overrides) ? schedule.date_range_overrides : [];
      dateRangeOverrides.forEach((item) => {{
        const windows = Array.isArray(item?.windows) ? item.windows : [];
        windows.forEach((windowItem) => {{
          const note = String(item?.note || "").trim();
          entries.push({{
            scope: "Range Override Window",
            key: `range:${{item?.start_date || ""}}|${{item?.end_date || ""}}|${{note}}|${{windowItem?.shift_label || ""}}|${{windowItem?.start_time || ""}}|${{windowItem?.end_time || ""}}`,
            label: `${{item?.start_date || "-"}} to ${{item?.end_date || "-"}}${{note ? ` (${{note}})` : ""}} | ${{windowItem?.shift_label || "-"}} ${{windowItem?.start_time || "-"}}-${{windowItem?.end_time || "-"}}`,
          }});
        }});
      }});
      return entries;
    }}

    function buildShiftScheduleOrderComparisons(schedule) {{
      const baseWindows = Array.isArray(schedule?.windows) ? schedule.windows : [];
      const baseSequence = baseWindows.map((item) => `${{item?.shift_label || "-"}} ${{item?.start_time || "-"}}-${{item?.end_time || "-"}}`);
      const dateOverrides = Array.isArray(schedule?.date_overrides) ? schedule.date_overrides : [];
      const dateSequence = dateOverrides.flatMap((item) => {{
        const note = String(item?.note || "").trim();
        const windows = Array.isArray(item?.windows) ? item.windows : [];
        return windows.map((windowItem) => `${{item?.date || "-"}}${{note ? ` (${{note}})` : ""}} | ${{windowItem?.shift_label || "-"}} ${{windowItem?.start_time || "-"}}-${{windowItem?.end_time || "-"}}`);
      }});
      const dateRangeOverrides = Array.isArray(schedule?.date_range_overrides) ? schedule.date_range_overrides : [];
      const rangeSequence = dateRangeOverrides.flatMap((item) => {{
        const note = String(item?.note || "").trim();
        const windows = Array.isArray(item?.windows) ? item.windows : [];
        return windows.map((windowItem) => `${{item?.start_date || "-"}} to ${{item?.end_date || "-"}}${{note ? ` (${{note}})` : ""}} | ${{windowItem?.shift_label || "-"}} ${{windowItem?.start_time || "-"}}-${{windowItem?.end_time || "-"}}`);
      }});
      return [
        {{
          scope: "Base Window Order",
          sequence: baseSequence,
        }},
        {{
          scope: "Date Override Order",
          sequence: dateSequence,
        }},
        {{
          scope: "Range Override Order",
          sequence: rangeSequence,
        }},
      ];
    }}

    function buildShiftScheduleImportDetailRows(currentDraft, importedSchedule) {{
      const detailRows = [];
      const currentEntries = buildShiftScheduleComparisonEntries(currentDraft);
      const importedEntries = buildShiftScheduleComparisonEntries(importedSchedule);
      const currentCounts = new Map();
      const importedCounts = new Map();
      const detailByKey = new Map();
      currentEntries.forEach((entry) => {{
        currentCounts.set(entry.key, (currentCounts.get(entry.key) || 0) + 1);
        detailByKey.set(entry.key, entry);
      }});
      importedEntries.forEach((entry) => {{
        importedCounts.set(entry.key, (importedCounts.get(entry.key) || 0) + 1);
        detailByKey.set(entry.key, entry);
      }});
      const orderedKeys = Array.from(detailByKey.keys()).sort((left, right) => left.localeCompare(right));
      orderedKeys.forEach((key) => {{
        const entry = detailByKey.get(key);
        const currentCount = currentCounts.get(key) || 0;
        const importedCount = importedCounts.get(key) || 0;
        const sharedCount = Math.min(currentCount, importedCount);
        for (let index = 0; index < sharedCount; index += 1) {{
          detailRows.push({{
            scope: entry?.scope || "Entry",
            status: "unchanged",
            label: entry?.label || key,
          }});
        }}
        for (let index = sharedCount; index < currentCount; index += 1) {{
          detailRows.push({{
            scope: entry?.scope || "Entry",
            status: "removed",
            label: entry?.label || key,
          }});
        }}
        for (let index = sharedCount; index < importedCount; index += 1) {{
          detailRows.push({{
            scope: entry?.scope || "Entry",
            status: "added",
            label: entry?.label || key,
          }});
        }}
      }});
      const currentOrders = buildShiftScheduleOrderComparisons(currentDraft);
      const importedOrders = buildShiftScheduleOrderComparisons(importedSchedule);
      currentOrders.forEach((currentOrder, index) => {{
        const importedOrder = importedOrders[index];
        const currentValue = currentOrder.sequence.join(" -> ");
        const importedValue = importedOrder.sequence.join(" -> ");
        if (currentValue !== importedValue) {{
          detailRows.push({{
            scope: currentOrder.scope,
            status: "reordered",
            label: `current: ${{currentValue || "(empty)"}} | imported: ${{importedValue || "(empty)"}}`,
          }});
        }}
      }});
      return detailRows;
    }}

    function formatShiftScheduleDelta(currentValue, importedValue) {{
      if (typeof currentValue === "number" && typeof importedValue === "number") {{
        const delta = importedValue - currentValue;
        if (delta === 0) {{
          return "0";
        }}
        return delta > 0 ? `+${{delta}}` : String(delta);
      }}
      return String(currentValue) === String(importedValue) ? "unchanged" : "changed";
    }}

    function getCurrentShiftScheduleDraftForPreview() {{
      try {{
        return buildShiftScheduleDraft();
      }} catch (_error) {{
        return currentShiftSchedule || {{
          workspace_id: workspaceInput.value.trim() || null,
          timezone: "UTC",
          windows: [],
          date_overrides: [],
          date_range_overrides: [],
          updated_at: null,
        }};
      }}
    }}

    function clearShiftScheduleImportPreview() {{
      pendingImportedShiftSchedule = null;
      shiftScheduleImportPreviewPanel.hidden = true;
      shiftScheduleImportPreviewMeta.textContent = "No pending import preview.";
      shiftScheduleImportPreviewText.textContent = "Choose a shift schedule JSON file to compare it against the current draft before applying.";
      shiftScheduleImportPreviewBody.innerHTML = '<tr><td colspan="4" class="mono">No import preview available.</td></tr>';
      shiftScheduleImportDetailBody.innerHTML = '<tr><td colspan="3" class="mono">No detailed import diff available.</td></tr>';
      applyShiftScheduleImportButton.disabled = true;
      discardShiftScheduleImportButton.disabled = true;
    }}

    function renderShiftScheduleImportPreview(importedSchedule, fileName = "") {{
      pendingImportedShiftSchedule = importedSchedule;
      const currentDraft = getCurrentShiftScheduleDraftForPreview();
      const previewRows = [
        {{
          label: "Timezone",
          current: currentDraft.timezone || "UTC",
          imported: importedSchedule.timezone || "UTC",
        }},
        {{
          label: "Base Windows",
          current: Array.isArray(currentDraft.windows) ? currentDraft.windows.length : 0,
          imported: Array.isArray(importedSchedule.windows) ? importedSchedule.windows.length : 0,
        }},
        {{
          label: "Date Override Groups",
          current: Array.isArray(currentDraft.date_overrides) ? currentDraft.date_overrides.length : 0,
          imported: Array.isArray(importedSchedule.date_overrides) ? importedSchedule.date_overrides.length : 0,
        }},
        {{
          label: "Date Override Windows",
          current: countShiftScheduleNestedWindows(currentDraft.date_overrides),
          imported: countShiftScheduleNestedWindows(importedSchedule.date_overrides),
        }},
        {{
          label: "Range Override Groups",
          current: Array.isArray(currentDraft.date_range_overrides) ? currentDraft.date_range_overrides.length : 0,
          imported: Array.isArray(importedSchedule.date_range_overrides) ? importedSchedule.date_range_overrides.length : 0,
        }},
        {{
          label: "Range Override Windows",
          current: countShiftScheduleNestedWindows(currentDraft.date_range_overrides),
          imported: countShiftScheduleNestedWindows(importedSchedule.date_range_overrides),
        }},
      ];
      shiftScheduleImportPreviewPanel.hidden = false;
      shiftScheduleImportPreviewMeta.textContent = [
        `workspace=${{importedSchedule.workspace_id || workspaceInput.value.trim() || "n/a"}}`,
        `file=${{fileName || "manual-import"}}`,
        `timezone=${{importedSchedule.timezone || "UTC"}}`,
      ].join(" | ");
      shiftScheduleImportPreviewText.textContent = "Import preview is ready. Apply Import to Draft to replace the current draft, then Save Schedule to persist it to the workspace.";
      shiftScheduleImportPreviewBody.innerHTML = previewRows.map((row) => `
        <tr>
          <td>${{escapeHtml(String(row.label))}}</td>
          <td class="mono">${{escapeHtml(String(row.current))}}</td>
          <td class="mono">${{escapeHtml(String(row.imported))}}</td>
          <td class="mono">${{escapeHtml(formatShiftScheduleDelta(row.current, row.imported))}}</td>
        </tr>
      `).join("");
      const detailRows = buildShiftScheduleImportDetailRows(currentDraft, importedSchedule);
      shiftScheduleImportDetailBody.innerHTML = detailRows.length === 0
        ? '<tr><td colspan="3" class="mono">No detailed import diff available.</td></tr>'
        : detailRows.map((row) => `
          <tr>
            <td>${{escapeHtml(String(row.scope))}}</td>
            <td class="mono">${{escapeHtml(String(row.status))}}</td>
            <td>${{escapeHtml(String(row.label))}}</td>
          </tr>
        `).join("");
      applyShiftScheduleImportButton.disabled = false;
      discardShiftScheduleImportButton.disabled = false;
    }}

    function clearShiftScheduleBaseForm() {{
      shiftScheduleBaseLabelInput.value = "";
      shiftScheduleBaseStartInput.value = "";
      shiftScheduleBaseEndInput.value = "";
    }}

    function clearShiftScheduleDateOverrideForm() {{
      shiftScheduleDateOverrideDateInput.value = "";
      shiftScheduleDateOverrideNoteInput.value = "";
      shiftScheduleDateOverrideLabelInput.value = "";
      shiftScheduleDateOverrideStartInput.value = "";
      shiftScheduleDateOverrideEndInput.value = "";
    }}

    function clearShiftScheduleRangeOverrideForm() {{
      shiftScheduleRangeOverrideStartDateInput.value = "";
      shiftScheduleRangeOverrideEndDateInput.value = "";
      shiftScheduleRangeOverrideNoteInput.value = "";
      shiftScheduleRangeOverrideLabelInput.value = "";
      shiftScheduleRangeOverrideStartInput.value = "";
      shiftScheduleRangeOverrideEndInput.value = "";
    }}

    function renderShiftScheduleEditor(schedule) {{
      currentShiftSchedule = schedule || null;
      shiftScheduleTimezoneInput.value = String(schedule?.timezone || "UTC");
      shiftScheduleWindowsInput.value = formatJsonEditorValue(schedule?.windows || []);
      shiftScheduleDateOverridesInput.value = formatJsonEditorValue(schedule?.date_overrides || []);
      shiftScheduleDateRangeOverridesInput.value = formatJsonEditorValue(schedule?.date_range_overrides || []);
      renderShiftScheduleStructuredTables(schedule || {{
        windows: [],
        date_overrides: [],
        date_range_overrides: [],
      }});
      const metaParts = [
        `timezone=${{shiftScheduleTimezoneInput.value || "UTC"}}`,
        `windows=${{(schedule?.windows || []).length}}`,
        `dates=${{(schedule?.date_overrides || []).length}}`,
        `ranges=${{(schedule?.date_range_overrides || []).length}}`,
      ];
      if (schedule?.updated_at) {{
        metaParts.push(`updated=${{schedule.updated_at}}`);
      }}
      shiftScheduleMeta.textContent = metaParts.join(" | ");
      clearShiftScheduleButton.disabled = !Boolean(
        schedule
        && (
          (schedule.windows || []).length > 0
          || (schedule.date_overrides || []).length > 0
          || (schedule.date_range_overrides || []).length > 0
          || schedule.updated_at
        )
      );
    }}

    function syncShiftScheduleDraftFromEditors() {{
      try {{
        const draft = buildShiftScheduleDraft();
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Structured editor synced from JSON draft.", "success");
        return draft;
      }} catch (error) {{
        renderShiftScheduleDraftParseError(error.message || String(error));
        setShiftScheduleStatus(error.message || String(error), "error");
        return null;
      }}
    }}

    async function copyShiftScheduleJson() {{
      try {{
        const draft = buildShiftScheduleDraft();
        await copyTextToClipboard(JSON.stringify(draft, null, 2));
        errorBox.style.display = "none";
        setShiftScheduleStatus("Copied shift schedule draft as JSON.", "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function exportShiftScheduleJson() {{
      try {{
        const draft = buildShiftScheduleDraft();
        const filenameBase = buildShiftScheduleExportFilename(draft);
        downloadTextFile(
          `${{filenameBase}}.json`,
          JSON.stringify(draft, null, 2),
          "application/json;charset=utf-8",
        );
        errorBox.style.display = "none";
        setShiftScheduleStatus("Exported shift schedule draft as JSON.", "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function promptShiftScheduleJsonImport() {{
      shiftScheduleImportInput.click();
    }}

    async function importShiftScheduleJson(event) {{
      const target = event.target;
      const file = target?.files?.[0];
      if (!file) {{
        return;
      }}
      try {{
        const rawText = await file.text();
        const parsed = JSON.parse(rawText);
        const importedSchedule = normalizeImportedShiftSchedule(parsed);
        renderShiftScheduleImportPreview(importedSchedule, file.name);
        errorBox.style.display = "none";
        setShiftScheduleStatus(
          `Previewed shift schedule import from ${{file.name}}. Apply Import to Draft to use it.`,
          "success",
        );
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
      }} finally {{
        if (target) {{
          target.value = "";
        }}
      }}
    }}

    function applyShiftScheduleImportPreview() {{
      if (!pendingImportedShiftSchedule) {{
        setShiftScheduleStatus("No import preview is available to apply.", "error");
        return;
      }}
      renderShiftScheduleEditor(pendingImportedShiftSchedule);
      clearShiftScheduleImportPreview();
      errorBox.style.display = "none";
      setShiftScheduleStatus("Applied imported shift schedule to the current draft. Save Schedule to persist it.", "success");
    }}

    function discardShiftScheduleImportPreview() {{
      if (!pendingImportedShiftSchedule) {{
        setShiftScheduleStatus("No import preview is available to discard.");
        return;
      }}
      clearShiftScheduleImportPreview();
      errorBox.style.display = "none";
      setShiftScheduleStatus("Discarded pending shift schedule import preview.", "success");
    }}

    function buildBaseWindowFromQuickForm() {{
      const shiftLabel = String(shiftScheduleBaseLabelInput.value || "").trim();
      const startTime = String(shiftScheduleBaseStartInput.value || "").trim();
      const endTime = String(shiftScheduleBaseEndInput.value || "").trim();
      if (!shiftLabel) {{
        throw new Error("Base window label is required.");
      }}
      if (!startTime || !endTime) {{
        throw new Error("Base window start and end times are required.");
      }}
      return {{
        shift_label: shiftLabel,
        start_time: startTime,
        end_time: endTime,
      }};
    }}

    function buildDateOverrideWindowFromQuickForm() {{
      const date = String(shiftScheduleDateOverrideDateInput.value || "").trim();
      const note = String(shiftScheduleDateOverrideNoteInput.value || "").trim();
      const shiftLabel = String(shiftScheduleDateOverrideLabelInput.value || "").trim();
      const startTime = String(shiftScheduleDateOverrideStartInput.value || "").trim();
      const endTime = String(shiftScheduleDateOverrideEndInput.value || "").trim();
      if (!date) {{
        throw new Error("Override date is required.");
      }}
      if (!shiftLabel) {{
        throw new Error("Date override window label is required.");
      }}
      if (!startTime || !endTime) {{
        throw new Error("Date override start and end times are required.");
      }}
      return {{
        date,
        note: note || null,
        window: {{
          shift_label: shiftLabel,
          start_time: startTime,
          end_time: endTime,
        }},
      }};
    }}

    function buildRangeOverrideWindowFromQuickForm() {{
      const startDate = String(shiftScheduleRangeOverrideStartDateInput.value || "").trim();
      const endDate = String(shiftScheduleRangeOverrideEndDateInput.value || "").trim();
      const note = String(shiftScheduleRangeOverrideNoteInput.value || "").trim();
      const shiftLabel = String(shiftScheduleRangeOverrideLabelInput.value || "").trim();
      const startTime = String(shiftScheduleRangeOverrideStartInput.value || "").trim();
      const endTime = String(shiftScheduleRangeOverrideEndInput.value || "").trim();
      if (!startDate || !endDate) {{
        throw new Error("Range override start and end dates are required.");
      }}
      if (!shiftLabel) {{
        throw new Error("Range override window label is required.");
      }}
      if (!startTime || !endTime) {{
        throw new Error("Range override start and end times are required.");
      }}
      return {{
        start_date: startDate,
        end_date: endDate,
        note: note || null,
        window: {{
          shift_label: shiftLabel,
          start_time: startTime,
          end_time: endTime,
        }},
      }};
    }}

    function addShiftScheduleBaseWindow() {{
      try {{
        const draft = buildShiftScheduleDraft();
        const nextWindow = buildBaseWindowFromQuickForm();
        const duplicateWindow = draft.windows.find((item) => String(item?.shift_label || "").trim() === nextWindow.shift_label);
        if (duplicateWindow) {{
          throw new Error(`Base window label ${{nextWindow.shift_label}} already exists in the draft.`);
        }}
        draft.windows = draft.windows.concat([nextWindow]);
        renderShiftScheduleEditor(draft);
        clearShiftScheduleBaseForm();
        setShiftScheduleStatus(`Added base window ${{nextWindow.shift_label}}.`, "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function addShiftScheduleDateOverrideWindow() {{
      try {{
        const draft = buildShiftScheduleDraft();
        const nextOverride = buildDateOverrideWindowFromQuickForm();
        const existingIndex = draft.date_overrides.findIndex((item) => String(item?.date || "").trim() === nextOverride.date);
        if (existingIndex >= 0) {{
          const existing = draft.date_overrides[existingIndex];
          draft.date_overrides[existingIndex] = {{
            ...existing,
            note: nextOverride.note || existing?.note || null,
            windows: (Array.isArray(existing?.windows) ? existing.windows : []).concat([nextOverride.window]),
          }};
        }} else {{
          draft.date_overrides = draft.date_overrides.concat([{{
            date: nextOverride.date,
            note: nextOverride.note,
            windows: [nextOverride.window],
          }}]);
        }}
        renderShiftScheduleEditor(draft);
        clearShiftScheduleDateOverrideForm();
        setShiftScheduleStatus(`Added date override window for ${{nextOverride.date}}.`, "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function addShiftScheduleRangeOverrideWindow() {{
      try {{
        const draft = buildShiftScheduleDraft();
        const nextOverride = buildRangeOverrideWindowFromQuickForm();
        const existingIndex = draft.date_range_overrides.findIndex(
          (item) => String(item?.start_date || "").trim() === nextOverride.start_date
            && String(item?.end_date || "").trim() === nextOverride.end_date,
        );
        if (existingIndex >= 0) {{
          const existing = draft.date_range_overrides[existingIndex];
          draft.date_range_overrides[existingIndex] = {{
            ...existing,
            note: nextOverride.note || existing?.note || null,
            windows: (Array.isArray(existing?.windows) ? existing.windows : []).concat([nextOverride.window]),
          }};
        }} else {{
          draft.date_range_overrides = draft.date_range_overrides.concat([{{
            start_date: nextOverride.start_date,
            end_date: nextOverride.end_date,
            note: nextOverride.note,
            windows: [nextOverride.window],
          }}]);
        }}
        renderShiftScheduleEditor(draft);
        clearShiftScheduleRangeOverrideForm();
        setShiftScheduleStatus(
          `Added range override window for ${{nextOverride.start_date}} to ${{nextOverride.end_date}}.`,
          "success",
        );
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function editShiftScheduleBaseWindow(windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        const selectedWindow = draft.windows[windowIndex];
        if (!selectedWindow) {{
          throw new Error("Selected base window is no longer available.");
        }}
        shiftScheduleBaseLabelInput.value = String(selectedWindow.shift_label || "");
        shiftScheduleBaseStartInput.value = String(selectedWindow.start_time || "");
        shiftScheduleBaseEndInput.value = String(selectedWindow.end_time || "");
        draft.windows = draft.windows.filter((_item, index) => index !== windowIndex);
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Loaded base window into form for editing.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function moveShiftScheduleBaseWindow(windowIndex, direction) {{
      try {{
        const draft = buildShiftScheduleDraft();
        const targetIndex = direction === "up" ? windowIndex - 1 : windowIndex + 1;
        draft.windows = moveArrayItem(draft.windows, windowIndex, targetIndex);
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus(`Moved base window ${{direction}} in draft order.`, "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function removeShiftScheduleBaseWindow(windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        draft.windows = draft.windows.filter((_item, index) => index !== windowIndex);
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Removed base window from draft.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function editShiftScheduleDateOverrideWindow(overrideIndex, windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        const selectedOverride = draft.date_overrides[overrideIndex];
        const selectedWindow = Array.isArray(selectedOverride?.windows) ? selectedOverride.windows[windowIndex] : null;
        if (!selectedOverride || !selectedWindow) {{
          throw new Error("Selected date override window is no longer available.");
        }}
        shiftScheduleDateOverrideDateInput.value = String(selectedOverride.date || "");
        shiftScheduleDateOverrideNoteInput.value = String(selectedOverride.note || "");
        shiftScheduleDateOverrideLabelInput.value = String(selectedWindow.shift_label || "");
        shiftScheduleDateOverrideStartInput.value = String(selectedWindow.start_time || "");
        shiftScheduleDateOverrideEndInput.value = String(selectedWindow.end_time || "");
        draft.date_overrides = draft.date_overrides.flatMap((item, index) => {{
          if (index !== overrideIndex) {{
            return [item];
          }}
          const nextWindows = (Array.isArray(item?.windows) ? item.windows : []).filter(
            (_windowItem, nestedIndex) => nestedIndex !== windowIndex,
          );
          if (nextWindows.length === 0) {{
            return [];
          }}
          return [{{
            ...item,
            windows: nextWindows,
          }}];
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Loaded date override window into form for editing.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function moveShiftScheduleDateOverrideWindow(overrideIndex, windowIndex, direction) {{
      try {{
        const draft = buildShiftScheduleDraft();
        draft.date_overrides = draft.date_overrides.map((item, index) => {{
          if (index !== overrideIndex) {{
            return item;
          }}
          const windows = Array.isArray(item?.windows) ? item.windows : [];
          const targetIndex = direction === "up" ? windowIndex - 1 : windowIndex + 1;
          return {{
            ...item,
            windows: moveArrayItem(windows, windowIndex, targetIndex),
          }};
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus(`Moved date override window ${{direction}} in draft order.`, "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function removeShiftScheduleDateOverrideWindow(overrideIndex, windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        draft.date_overrides = draft.date_overrides.flatMap((item, index) => {{
          if (index !== overrideIndex) {{
            return [item];
          }}
          const nextWindows = (Array.isArray(item?.windows) ? item.windows : []).filter(
            (_windowItem, nestedIndex) => nestedIndex !== windowIndex,
          );
          if (nextWindows.length === 0) {{
            return [];
          }}
          return [{{
            ...item,
            windows: nextWindows,
          }}];
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Removed date override window from draft.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function editShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        const selectedOverride = draft.date_range_overrides[overrideIndex];
        const selectedWindow = Array.isArray(selectedOverride?.windows) ? selectedOverride.windows[windowIndex] : null;
        if (!selectedOverride || !selectedWindow) {{
          throw new Error("Selected range override window is no longer available.");
        }}
        shiftScheduleRangeOverrideStartDateInput.value = String(selectedOverride.start_date || "");
        shiftScheduleRangeOverrideEndDateInput.value = String(selectedOverride.end_date || "");
        shiftScheduleRangeOverrideNoteInput.value = String(selectedOverride.note || "");
        shiftScheduleRangeOverrideLabelInput.value = String(selectedWindow.shift_label || "");
        shiftScheduleRangeOverrideStartInput.value = String(selectedWindow.start_time || "");
        shiftScheduleRangeOverrideEndInput.value = String(selectedWindow.end_time || "");
        draft.date_range_overrides = draft.date_range_overrides.flatMap((item, index) => {{
          if (index !== overrideIndex) {{
            return [item];
          }}
          const nextWindows = (Array.isArray(item?.windows) ? item.windows : []).filter(
            (_windowItem, nestedIndex) => nestedIndex !== windowIndex,
          );
          if (nextWindows.length === 0) {{
            return [];
          }}
          return [{{
            ...item,
            windows: nextWindows,
          }}];
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Loaded range override window into form for editing.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function moveShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex, direction) {{
      try {{
        const draft = buildShiftScheduleDraft();
        draft.date_range_overrides = draft.date_range_overrides.map((item, index) => {{
          if (index !== overrideIndex) {{
            return item;
          }}
          const windows = Array.isArray(item?.windows) ? item.windows : [];
          const targetIndex = direction === "up" ? windowIndex - 1 : windowIndex + 1;
          return {{
            ...item,
            windows: moveArrayItem(windows, windowIndex, targetIndex),
          }};
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus(`Moved range override window ${{direction}} in draft order.`, "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    function removeShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex) {{
      try {{
        const draft = buildShiftScheduleDraft();
        draft.date_range_overrides = draft.date_range_overrides.flatMap((item, index) => {{
          if (index !== overrideIndex) {{
            return [item];
          }}
          const nextWindows = (Array.isArray(item?.windows) ? item.windows : []).filter(
            (_windowItem, nestedIndex) => nestedIndex !== windowIndex,
          );
          if (nextWindows.length === 0) {{
            return [];
          }}
          return [{{
            ...item,
            windows: nextWindows,
          }}];
        }});
        renderShiftScheduleEditor(draft);
        setShiftScheduleStatus("Removed range override window from draft.", "success");
      }} catch (error) {{
        setShiftScheduleStatus(error.message || String(error), "error");
      }}
    }}

    async function fetchReplayWorkerMonitorShiftSchedule() {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        const emptySchedule = {{
          workspace_id: null,
          timezone: "UTC",
          windows: [],
          date_overrides: [],
          date_range_overrides: [],
          updated_at: null,
        }};
        renderShiftScheduleEditor(emptySchedule);
        return emptySchedule;
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-shift-schedule?${{params.toString()}}`, {{
        headers: {{ "Accept": "application/json" }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      const schedule = payload.data || {{
        workspace_id: workspaceId,
        timezone: "UTC",
        windows: [],
        date_overrides: [],
        date_range_overrides: [],
        updated_at: null,
      }};
      renderShiftScheduleEditor(schedule);
      return schedule;
    }}

    async function refreshShiftScheduleEditor() {{
      loadShiftScheduleButton.disabled = true;
      try {{
        const workspaceId = workspaceInput.value.trim();
        if (!workspaceId) {{
          const emptySchedule = {{
            workspace_id: null,
            timezone: "UTC",
            windows: [],
            date_overrides: [],
            date_range_overrides: [],
            updated_at: null,
          }};
          renderShiftScheduleEditor(emptySchedule);
          clearShiftScheduleImportPreview();
          errorBox.style.display = "none";
          setShiftScheduleStatus("No workspace selected. Editor is showing an empty shift schedule.");
          return emptySchedule;
        }}
        setShiftScheduleStatus("Loading shift schedule...");
        const schedule = await fetchReplayWorkerMonitorShiftSchedule();
        clearShiftScheduleImportPreview();
        errorBox.style.display = "none";
        setShiftScheduleStatus("Shift schedule loaded.", "success");
        return schedule;
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
        return null;
      }} finally {{
        loadShiftScheduleButton.disabled = false;
      }}
    }}

    function buildShiftScheduleCommand() {{
      const timezone = String(shiftScheduleTimezoneInput.value || "").trim() || "UTC";
      return {{
        timezone,
        windows: parseJsonArrayEditorValue(shiftScheduleWindowsInput.value, "Base Windows JSON"),
        date_overrides: parseJsonArrayEditorValue(shiftScheduleDateOverridesInput.value, "Date Overrides JSON"),
        date_range_overrides: parseJsonArrayEditorValue(shiftScheduleDateRangeOverridesInput.value, "Range Overrides JSON"),
      }};
    }}

    async function syncAfterShiftScheduleChange() {{
      if (getPolicyAuditPresetScope() === "workspace") {{
        await refreshWorkspacePolicyAuditPresets();
        applyInitialPolicyAuditPresetSelection();
      }} else {{
        await refreshPolicyAuditShiftResolution();
        syncPolicyAuditPresetControls();
      }}
      await render({{ highlightFresh: true }});
    }}

    async function saveShiftSchedule() {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        setShiftScheduleStatus("workspace_id is required before saving a shift schedule.", "error");
        return;
      }}
      try {{
        const command = buildShiftScheduleCommand();
        saveShiftScheduleButton.disabled = true;
        clearShiftScheduleButton.disabled = true;
        setShiftScheduleStatus("Saving shift schedule...");
        const params = new URLSearchParams({{ workspace_id: workspaceId }});
        const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-shift-schedule?${{params.toString()}}`, {{
          method: "PUT",
          headers: {{
            "Accept": "application/json",
            "Content-Type": "application/json",
          }},
          credentials: "same-origin",
          body: JSON.stringify(command),
        }});
        if (!response.ok) {{
          const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
          throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
        }}
        const payload = await response.json();
        renderShiftScheduleEditor(payload.data);
        clearShiftScheduleImportPreview();
        await syncAfterShiftScheduleChange();
        errorBox.style.display = "none";
        setShiftScheduleStatus("Shift schedule saved.", "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
      }} finally {{
        saveShiftScheduleButton.disabled = false;
        clearShiftScheduleButton.disabled = false;
      }}
    }}

    async function clearShiftSchedule() {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        setShiftScheduleStatus("workspace_id is required before clearing a shift schedule.", "error");
        return;
      }}
      try {{
        loadShiftScheduleButton.disabled = true;
        saveShiftScheduleButton.disabled = true;
        clearShiftScheduleButton.disabled = true;
        setShiftScheduleStatus("Clearing shift schedule...");
        const params = new URLSearchParams({{ workspace_id: workspaceId }});
        const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-shift-schedule?${{params.toString()}}`, {{
          method: "DELETE",
          headers: {{ "Accept": "application/json" }},
          credentials: "same-origin",
        }});
        if (!response.ok) {{
          const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
          throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
        }}
        await response.json();
        renderShiftScheduleEditor({{
          workspace_id: workspaceId,
          timezone: "UTC",
          windows: [],
          date_overrides: [],
          date_range_overrides: [],
          updated_at: null,
        }});
        clearShiftScheduleImportPreview();
        await syncAfterShiftScheduleChange();
        errorBox.style.display = "none";
        setShiftScheduleStatus("Shift schedule cleared.", "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setShiftScheduleStatus(error.message || String(error), "error");
      }} finally {{
        loadShiftScheduleButton.disabled = false;
        saveShiftScheduleButton.disabled = false;
        clearShiftScheduleButton.disabled = false;
      }}
    }}

    async function refreshWorkspacePolicyAuditPresets(preferredName = "") {{
      await refreshPolicyAuditShiftResolution();
      policyAuditWorkspacePresets = await fetchWorkspacePolicyAuditPresets();
      if (getPolicyAuditPresetScope() === "workspace") {{
        renderPolicyAuditPresetOptions(preferredName);
      }}
      return policyAuditWorkspacePresets;
    }}

    async function upsertWorkspacePolicyAuditPreset(presetName, snapshot) {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        throw new Error("workspace_id is required to save a shared preset.");
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-presets/${{encodeURIComponent(presetName)}}?${{params.toString()}}`, {{
        method: "PUT",
        headers: {{
          "Accept": "application/json",
          "Content-Type": "application/json",
        }},
        credentials: "same-origin",
        body: JSON.stringify({{
          history_limit: Number.parseInt(snapshot.history_limit, 10),
          actor_user_id: snapshot.actor_user_id || null,
          request_id: snapshot.request_id || null,
          policy_audit_limit: Number.parseInt(snapshot.policy_audit_limit, 10),
          policy_audit_copy_format: snapshot.policy_audit_copy_format,
          policy_audit_include_summary: snapshot.policy_audit_include_summary !== false,
        }}),
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    async function deleteWorkspacePolicyAuditPreset(presetName) {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        throw new Error("workspace_id is required to delete a shared preset.");
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-presets/${{encodeURIComponent(presetName)}}?${{params.toString()}}`, {{
        method: "DELETE",
        headers: {{
          "Accept": "application/json",
        }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    async function setWorkspacePolicyAuditDefaultPreset(presetName) {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        throw new Error("workspace_id is required to set a shared default preset.");
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const shiftLabel = getEffectivePolicyAuditShiftLabel();
      if (shiftLabel) {{
        params.set("shift_label", shiftLabel);
      }}
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-default-preset/${{encodeURIComponent(presetName)}}?${{params.toString()}}`, {{
        method: "PUT",
        headers: {{
          "Accept": "application/json",
        }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    async function clearWorkspacePolicyAuditDefaultPreset() {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        throw new Error("workspace_id is required to clear a shared default preset.");
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const shiftLabel = getEffectivePolicyAuditShiftLabel();
      if (shiftLabel) {{
        params.set("shift_label", shiftLabel);
      }}
      const response = await fetch(`/api/v1/opsgraph/replays/worker-monitor-default-preset?${{params.toString()}}`, {{
        method: "DELETE",
        headers: {{
          "Accept": "application/json",
        }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    function syncPolicyAuditPresetControls() {{
      syncActivePolicyAuditPresets();
      const defaultScope = getPolicyAuditDefaultSource();
      const scopedDefaultPresetName = getScopedWorkspacePolicyAuditDefaultPresetName();
      const hasSelectedPreset = Boolean(getSelectedPolicyAuditPresetName());
      const requiresWorkspace = getPolicyAuditPresetScope() === "workspace";
      const workspaceReady = workspaceInput.value.trim() !== "";
      const selectedPresetName = getSelectedPolicyAuditPresetName();
      const selectedPreset = selectedPresetName ? policyAuditPresets[selectedPresetName] : null;
      setPolicyAuditDefaultPresetButton.textContent = getPolicyAuditShiftLabel()
        ? "Set Shift Default"
        : "Set Workspace Default";
      clearPolicyAuditDefaultPresetButton.textContent = getPolicyAuditShiftLabel()
        ? "Clear Shift Default"
        : "Clear Default";
      savePolicyAuditPresetButton.disabled = (
        normalizePolicyAuditPresetName(policyAuditPresetNameInput.value) === ""
        || (requiresWorkspace && !workspaceReady)
      );
      loadPolicyAuditPresetButton.disabled = !hasSelectedPreset;
      deletePolicyAuditPresetButton.disabled = !hasSelectedPreset;
      setPolicyAuditDefaultPresetButton.disabled = (
        !requiresWorkspace
        || !workspaceReady
        || !hasSelectedPreset
        || Boolean(selectedPreset?.is_default && selectedPreset?.default_source === defaultScope)
      );
      clearPolicyAuditDefaultPresetButton.disabled = (
        !requiresWorkspace
        || !workspaceReady
        || !scopedDefaultPresetName
      );
      renderPolicyAuditShiftMeta();
    }}

    function renderPolicyAuditPresetOptions(preferredName = "") {{
      syncActivePolicyAuditPresets();
      const selectedName = preferredName || getSelectedPolicyAuditPresetName();
      const presetNames = Object.keys(policyAuditPresets).sort((left, right) => left.localeCompare(right));
      if (presetNames.length === 0) {{
        policyAuditPresetSelect.innerHTML = `<option value="">${{getPolicyAuditPresetScope() === "workspace" ? "No workspace presets" : "No browser presets"}}</option>`;
        policyAuditPresetSelect.value = "";
        syncPolicyAuditPresetControls();
        return;
      }}
      policyAuditPresetSelect.innerHTML = [
        `<option value="">${{getPolicyAuditPresetScope() === "workspace" ? "Select workspace preset" : "Select browser preset"}}</option>`,
        ...presetNames.map((presetName) => {{
          const defaultSource = policyAuditPresets[presetName]?.default_source || "none";
          const label = defaultSource === "shift_default"
            ? `${{presetName}} (shift default)`
            : (
              defaultSource === "workspace_default"
                ? `${{presetName}} (workspace default)`
                : presetName
            );
          return `<option value="${{escapeHtml(presetName)}}">${{escapeHtml(label)}}</option>`;
        }}),
      ].join("");
      policyAuditPresetSelect.value = selectedName && policyAuditPresets[selectedName]
        ? selectedName
        : "";
      syncPolicyAuditPresetControls();
    }}

    function applyPolicyAuditPresetSnapshot(snapshot) {{
      workspaceInput.value = String(snapshot.workspace_id || "ops-ws-1");
      historyLimitInput.value = String(snapshot.history_limit || "10");
      policyAuditActorInput.value = String(snapshot.actor_user_id || "");
      policyAuditRequestInput.value = String(snapshot.request_id || "");
      policyAuditLimitInput.value = ["5", "10", "20", "50"].includes(String(snapshot.policy_audit_limit || ""))
        ? String(snapshot.policy_audit_limit)
        : "5";
      policyAuditCopyFormatInput.value = ["plain", "markdown", "slack"].includes(String(snapshot.policy_audit_copy_format || ""))
        ? String(snapshot.policy_audit_copy_format)
        : "plain";
      policyAuditIncludeSummaryInput.checked = snapshot.policy_audit_include_summary !== false;
    }}

    async function saveCurrentPolicyAuditPreset() {{
      const presetName = normalizePolicyAuditPresetName(policyAuditPresetNameInput.value);
      if (!presetName) {{
        setPolicyAuditActionStatus("Preset name is required before saving.", "error");
        return;
      }}
      const snapshot = buildPolicyAuditPresetSnapshot();
      try {{
        if (getPolicyAuditPresetScope() === "workspace") {{
          await upsertWorkspacePolicyAuditPreset(presetName, snapshot);
          await refreshWorkspacePolicyAuditPresets(presetName);
        }} else {{
          policyAuditBrowserPresets[presetName] = snapshot;
          if (!persistBrowserPolicyAuditPresets()) {{
            return;
          }}
          renderPolicyAuditPresetOptions(presetName);
        }}
        policyAuditPresetNameInput.value = presetName;
        updateQueryString();
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(
          `Saved ${{getPolicyAuditPresetScope() === "workspace" ? "workspace" : "browser"}} preset ${{presetName}}.`,
          "success",
        );
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    async function loadSelectedPolicyAuditPreset() {{
      const presetName = getSelectedPolicyAuditPresetName();
      if (!presetName) {{
        setPolicyAuditActionStatus("Select a saved preset before loading.", "error");
        return;
      }}
      applyPolicyAuditPresetSnapshot(policyAuditPresets[presetName]);
      policyAuditPresetNameInput.value = presetName;
      renderPolicyAuditPresetOptions(presetName);
      updateQueryString();
      await render();
      connectStream();
      errorBox.style.display = "none";
      setPolicyAuditActionStatus(`Loaded preset ${{presetName}}.`, "success");
    }}

    async function deleteSelectedPolicyAuditPreset() {{
      const presetName = getSelectedPolicyAuditPresetName();
      if (!presetName) {{
        setPolicyAuditActionStatus("Select a saved preset before deleting.", "error");
        return;
      }}
      try {{
        if (getPolicyAuditPresetScope() === "workspace") {{
          await deleteWorkspacePolicyAuditPreset(presetName);
          await refreshWorkspacePolicyAuditPresets("");
        }} else {{
          delete policyAuditBrowserPresets[presetName];
          if (!persistBrowserPolicyAuditPresets()) {{
            return;
          }}
          renderPolicyAuditPresetOptions("");
        }}
        policyAuditPresetNameInput.value = "";
        updateQueryString();
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(
          `Deleted ${{getPolicyAuditPresetScope() === "workspace" ? "workspace" : "browser"}} preset ${{presetName}}.`,
          "success",
        );
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    async function setSelectedPolicyAuditDefaultPreset() {{
      const presetName = getSelectedPolicyAuditPresetName();
      if (!presetName) {{
        setPolicyAuditActionStatus("Select a workspace preset before setting it as default.", "error");
        return;
      }}
      if (getPolicyAuditPresetScope() !== "workspace") {{
        setPolicyAuditActionStatus("Switch preset scope to workspace before setting a shared default.", "error");
        return;
      }}
      try {{
        await setWorkspacePolicyAuditDefaultPreset(presetName);
        await refreshWorkspacePolicyAuditPresets(presetName);
        policyAuditPresetNameInput.value = presetName;
        updateQueryString();
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(`Set ${{getPolicyAuditDefaultScopeLabel()}} preset to ${{presetName}}.`, "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    async function clearSelectedPolicyAuditDefaultPreset() {{
      if (getPolicyAuditPresetScope() !== "workspace") {{
        setPolicyAuditActionStatus("Switch preset scope to workspace before clearing the shared default.", "error");
        return;
      }}
      try {{
        const previousDefaultPresetName = getScopedWorkspacePolicyAuditDefaultPresetName();
        await clearWorkspacePolicyAuditDefaultPreset();
        await refreshWorkspacePolicyAuditPresets(previousDefaultPresetName);
        updateQueryString();
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(`Cleared ${{getPolicyAuditDefaultScopeLabel()}} preset.`, "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    function applyInitialPolicyAuditPresetSelection() {{
      const currentSearch = getCurrentMonitorSearch();
      const requestedPresetName = normalizePolicyAuditPresetName(currentSearch.get("policy_audit_preset_name") || "");
      if (requestedPresetName && policyAuditPresets[requestedPresetName]) {{
        applyPolicyAuditPresetSnapshot(policyAuditPresets[requestedPresetName]);
        policyAuditPresetNameInput.value = requestedPresetName;
        renderPolicyAuditPresetOptions(requestedPresetName);
        return;
      }}
      if (getPolicyAuditPresetScope() === "workspace" && !hasExplicitPolicyAuditSelectionInQuery()) {{
        const defaultPresetName = getDefaultWorkspacePolicyAuditPresetName();
        if (defaultPresetName && policyAuditWorkspacePresets[defaultPresetName]) {{
          applyPolicyAuditPresetSnapshot(policyAuditWorkspacePresets[defaultPresetName]);
          policyAuditPresetNameInput.value = defaultPresetName;
          renderPolicyAuditPresetOptions(defaultPresetName);
          return;
        }}
      }}
      renderPolicyAuditPresetOptions(requestedPresetName);
    }}

    function sanitizeFilePart(value, fallback) {{
      const normalized = String(value || "").trim().replace(/[^a-zA-Z0-9._-]+/g, "-");
      return normalized || fallback;
    }}

    function escapeHtml(value) {{
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }}

    function formatAuditPayload(payload) {{
      if (!payload || Object.keys(payload).length === 0) {{
        return '<div class="foot">No payload recorded.</div>';
      }}
      return `<pre class="audit-json">${{escapeHtml(JSON.stringify(payload, null, 2))}}</pre>`;
    }}

    function renderPolicy(policy) {{
      const defaultWarning = policy?.default_warning_consecutive_failures ?? policy?.warning_consecutive_failures ?? 1;
      const defaultCritical = policy?.default_critical_consecutive_failures ?? policy?.critical_consecutive_failures ?? 3;
      const effectiveWarning = policy?.warning_consecutive_failures ?? defaultWarning;
      const effectiveCritical = policy?.critical_consecutive_failures ?? defaultCritical;
      currentPolicy = {{
        source: policy?.source || "default",
        warning: effectiveWarning,
        critical: effectiveCritical,
        defaultWarning,
        defaultCritical,
      }};
      warningThresholdInput.value = String(effectiveWarning);
      criticalThresholdInput.value = String(effectiveCritical);
      policySource.textContent = currentPolicy.source;
      policyUpdatedAt.textContent = policy?.updated_at || "-";
      policyMeta.textContent = `default=${{defaultWarning}}/${{defaultCritical}}`;
      policyHint.textContent = currentPolicy.source === "workspace_override"
        ? `Workspace override is active. Resetting will restore runtime default ${{defaultWarning}}/${{defaultCritical}}.`
        : `This workspace is using runtime default ${{defaultWarning}}/${{defaultCritical}}.`;
      resetPolicyButton.disabled = currentPolicy.source !== "workspace_override";
    }}

    function buildMonitorUrl(overrides = {{}}) {{
      const params = new URLSearchParams(window.location.search);
      const workspaceId = overrides.workspaceId ?? workspaceInput.value.trim();
      const historyLimit = overrides.historyLimit ?? historyLimitInput.value;
      const actorUserId = overrides.actorUserId ?? policyAuditActorInput.value.trim();
      const requestId = overrides.requestId ?? policyAuditRequestInput.value.trim();
      const policyAuditLimit = overrides.policyAuditLimit ?? policyAuditLimitInput.value;
      const policyAuditShiftSource = overrides.policyAuditShiftSource ?? getPolicyAuditShiftSource();
      const policyAuditShiftLabel = overrides.policyAuditShiftLabel ?? getPolicyAuditShiftLabel();
      if (workspaceId) {{
        params.set("workspace_id", workspaceId);
      }} else {{
        params.delete("workspace_id");
      }}
      params.set("history_limit", historyLimit);
      if (actorUserId) {{
        params.set("actor_user_id", actorUserId);
      }} else {{
        params.delete("actor_user_id");
      }}
      if (requestId) {{
        params.set("request_id", requestId);
      }} else {{
        params.delete("request_id");
      }}
      params.set("policy_audit_limit", policyAuditLimit);
      params.set("policy_audit_copy_format", getPolicyAuditCopyFormat());
      params.set("policy_audit_include_summary", getPolicyAuditIncludeSummary() ? "1" : "0");
      params.set("policy_audit_preset_scope", getPolicyAuditPresetScope());
      params.set("policy_audit_shift_source", policyAuditShiftSource);
      if (policyAuditShiftLabel) {{
        params.set("policy_audit_shift_label", policyAuditShiftLabel);
      }} else {{
        params.delete("policy_audit_shift_label");
      }}
      const selectedPresetName = getSelectedPolicyAuditPresetName();
      if (selectedPresetName) {{
        params.set("policy_audit_preset_name", selectedPresetName);
      }} else {{
        params.delete("policy_audit_preset_name");
      }}
      const query = params.toString();
      return `${{window.location.pathname}}${{query ? `?${{query}}` : ""}}`;
    }}

    function updateQueryString() {{
      window.history.replaceState(null, "", buildMonitorUrl());
    }}

    function buildMonitorAbsoluteUrl(overrides = {{}}) {{
      return new URL(buildMonitorUrl(overrides), window.location.origin).toString();
    }}

    async function copyTextToClipboard(value) {{
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {{
        await navigator.clipboard.writeText(value);
        return;
      }}
      const tempInput = document.createElement("textarea");
      tempInput.value = value;
      tempInput.setAttribute("readonly", "readonly");
      tempInput.style.position = "absolute";
      tempInput.style.left = "-9999px";
      document.body.appendChild(tempInput);
      tempInput.select();
      document.execCommand("copy");
      document.body.removeChild(tempInput);
    }}

    async function copyCurrentPolicyAuditLink() {{
      try {{
        updateQueryString();
        const absoluteUrl = new URL(buildMonitorUrl(), window.location.origin).toString();
        await copyTextToClipboard(absoluteUrl);
        errorBox.style.display = "none";
        setPolicyAuditActionStatus("Copied current audit filter link.", "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    function buildPolicyAuditContextText(item, monitorSummary, formatStyle = getPolicyAuditCopyFormat()) {{
      const actorLabel = item.actor_user_id
        ? `${{item.actor_user_id}} (${{item.actor_role || item.actor_type || "user"}})`
        : (item.actor_type || "system");
      const requestThresholds = `${{item.request_payload?.warning_consecutive_failures ?? '-'}} / ${{item.request_payload?.critical_consecutive_failures ?? '-'}}`;
      const resultThresholds = `${{item.result_payload?.warning_consecutive_failures ?? '-'}} / ${{item.result_payload?.critical_consecutive_failures ?? '-'}}`;
      const resultSource = item.result_payload?.source || "-";
      const workspaceValue = monitorSummary.workspace_id || workspaceInput.value.trim() || "-";
      const shiftLabelValue = monitorSummary.policy_audit_effective_shift_label || monitorSummary.policy_audit_shift_label || "-";
      const requestIdValue = item.request_id || "-";
      const recordedAtValue = item.created_at || "-";
      const actionValue = item.action_type || "-";
      const workerStatusValue = monitorSummary.current_status || "-";
      const alertLevelValue = monitorSummary.alert_level || "-";
      const monitorUrlValue = monitorSummary.monitor_absolute_url || buildMonitorAbsoluteUrl();
      if (formatStyle === "markdown") {{
        return [
          "## OpsGraph replay worker policy audit",
          "",
          "- Workspace: `" + workspaceValue + "`",
          "- Shift: `" + shiftLabelValue + "`",
          "- Request ID: `" + requestIdValue + "`",
          "- Actor: `" + actorLabel + "`",
          "- Recorded At: `" + recordedAtValue + "`",
          "- Action: `" + actionValue + "`",
          "- Requested Thresholds: `" + requestThresholds + "`",
          "- Result: `" + resultSource + " (" + resultThresholds + ")" + "`",
          "- Worker Status: `" + workerStatusValue + "`",
          "- Alert Level: `" + alertLevelValue + "`",
          "- Monitor: " + monitorUrlValue,
        ].join("\\n");
      }}
      if (formatStyle === "slack") {{
        return [
          "*OpsGraph replay worker policy audit*",
          "- *Workspace:* `" + workspaceValue + "`",
          "- *Shift:* `" + shiftLabelValue + "`",
          "- *Request ID:* `" + requestIdValue + "`",
          "- *Actor:* `" + actorLabel + "`",
          "- *Recorded At:* `" + recordedAtValue + "`",
          "- *Action:* `" + actionValue + "`",
          "- *Requested Thresholds:* `" + requestThresholds + "`",
          "- *Result:* `" + resultSource + " (" + resultThresholds + ")" + "`",
          "- *Worker Status:* `" + workerStatusValue + "`",
          "- *Alert Level:* `" + alertLevelValue + "`",
          "- *Monitor:* " + monitorUrlValue,
        ].join("\\n");
      }}
      if (false && formatStyle === "slack") {{
        return [
          "*OpsGraph replay worker policy audit*",
          "• *Workspace:* `" + workspaceValue + "`",
          "• *Request ID:* `" + requestIdValue + "`",
          "• *Actor:* `" + actorLabel + "`",
          "• *Recorded At:* `" + recordedAtValue + "`",
          "• *Action:* `" + actionValue + "`",
          "• *Requested Thresholds:* `" + requestThresholds + "`",
          "• *Result:* `" + resultSource + " (" + resultThresholds + ")" + "`",
          "• *Worker Status:* `" + workerStatusValue + "`",
          "• *Alert Level:* `" + alertLevelValue + "`",
          "• *Monitor:* " + monitorUrlValue,
        ].join("\\n");
      }}
      return [
        "OpsGraph replay worker policy audit",
        `workspace: ${{workspaceValue}}`,
        `shift: ${{shiftLabelValue}}`,
        `request_id: ${{requestIdValue}}`,
        `actor: ${{actorLabel}}`,
        `recorded_at: ${{recordedAtValue}}`,
        `action: ${{actionValue}}`,
        `requested_thresholds: ${{requestThresholds}}`,
        `result: ${{resultSource}} (${{resultThresholds}})`,
        `worker_status: ${{workerStatusValue}}`,
        `alert_level: ${{alertLevelValue}}`,
        `monitor: ${{monitorUrlValue}}`,
      ].join("\\n");
    }}

    async function copyLatestPolicyAuditContext() {{
      if (policyAuditItems.length === 0) {{
        setPolicyAuditActionStatus("No latest policy audit row is available to copy.");
        return;
      }}
      copyPolicyAuditContext(policyAuditItems[0], {{ successLabel: "latest policy audit context" }});
    }}

    async function copyPolicyAuditContext(item, {{ successLabel = "policy audit context" }} = {{}}) {{
      if (!item) {{
        setPolicyAuditActionStatus("Selected policy audit row is no longer loaded.");
        return;
      }}
      try {{
        const monitorSummary = buildPolicyAuditMonitorSummary();
        const copyFormat = getPolicyAuditCopyFormat();
        await copyTextToClipboard(buildPolicyAuditContextText(item, monitorSummary, copyFormat));
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(`Copied ${{successLabel}} as ${{copyFormat}}.`, "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    function copySinglePolicyAuditContext(auditId) {{
      copyPolicyAuditContext(findPolicyAuditItemById(auditId), {{ successLabel: "selected policy audit context" }});
    }}

    function downloadTextFile(filename, content, mimeType) {{
      const blob = new Blob([content], {{ type: mimeType }});
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
    }}

    function buildPolicyAuditExportRows(sourceItems = policyAuditItems) {{
      return sourceItems.map((item) => ({{
        id: item.id || null,
        created_at: item.created_at || null,
        workspace_id: item.workspace_id || workspaceInput.value.trim() || null,
        action_type: item.action_type || null,
        actor_type: item.actor_type || null,
        actor_user_id: item.actor_user_id || null,
        actor_role: item.actor_role || null,
        session_id: item.session_id || null,
        request_id: item.request_id || null,
        idempotency_key: item.idempotency_key || null,
        subject_type: item.subject_type || null,
        subject_id: item.subject_id || null,
        request_warning_consecutive_failures: item.request_payload?.warning_consecutive_failures ?? null,
        request_critical_consecutive_failures: item.request_payload?.critical_consecutive_failures ?? null,
        result_source: item.result_payload?.source || null,
        result_warning_consecutive_failures: item.result_payload?.warning_consecutive_failures ?? null,
        result_critical_consecutive_failures: item.result_payload?.critical_consecutive_failures ?? null,
        request_payload_json: item.request_payload || null,
        result_payload_json: item.result_payload || null,
      }}));
    }}

    function buildPolicyAuditMonitorSummary() {{
      const monitorRelativeUrl = buildMonitorUrl();
      const monitorAbsoluteUrl = buildMonitorAbsoluteUrl();
      return {{
        exported_at: new Date().toISOString(),
        copy_format: getPolicyAuditCopyFormat(),
        policy_audit_preset_scope: getPolicyAuditPresetScope(),
        policy_audit_shift_source: getPolicyAuditShiftSource(),
        policy_audit_shift_label: getPolicyAuditShiftLabel() || null,
        policy_audit_effective_shift_label: getEffectivePolicyAuditShiftLabel() || null,
        policy_audit_resolved_shift_source: currentResolvedPolicyAuditShift?.source || null,
        policy_audit_resolved_shift_timezone: currentResolvedPolicyAuditShift?.timezone || null,
        policy_audit_resolved_shift_date: currentResolvedPolicyAuditShift?.override_date || null,
        policy_audit_resolved_shift_range_start_date: currentResolvedPolicyAuditShift?.override_range_start_date || null,
        policy_audit_resolved_shift_range_end_date: currentResolvedPolicyAuditShift?.override_range_end_date || null,
        policy_audit_resolved_shift_note: currentResolvedPolicyAuditShift?.override_note || null,
        policy_audit_preset_name: getSelectedPolicyAuditPresetName() || normalizePolicyAuditPresetName(policyAuditPresetNameInput.value) || null,
        policy_audit_preset_is_default: Boolean(
          getSelectedPolicyAuditPresetName()
          && policyAuditPresets[getSelectedPolicyAuditPresetName()]?.is_default
        ),
        policy_audit_preset_default_source: (
          getSelectedPolicyAuditPresetName()
            ? policyAuditPresets[getSelectedPolicyAuditPresetName()]?.default_source || "none"
            : "none"
        ),
        monitor_relative_url: monitorRelativeUrl,
        monitor_absolute_url: monitorAbsoluteUrl,
        workspace_id: currentWorkerStatusData?.workspace_id || workspaceInput.value.trim() || null,
        current_status: currentWorkerStatusData?.current?.status || null,
        current_last_seen_at: currentWorkerStatusData?.current?.last_seen_at || null,
        current_remaining_queued_count: currentWorkerStatusData?.current?.remaining_queued_count ?? null,
        current_consecutive_failures: currentWorkerStatusData?.current?.consecutive_failures ?? null,
        alert_level: currentWorkerStatusData?.alert?.level || null,
        alert_headline: currentWorkerStatusData?.alert?.headline || null,
        alert_detail: currentWorkerStatusData?.alert?.detail || null,
        latest_failure_status: currentWorkerStatusData?.alert?.latest_failure_status || null,
        latest_failure_at: currentWorkerStatusData?.alert?.latest_failure_at || null,
        latest_failure_message: currentWorkerStatusData?.alert?.latest_failure_message || null,
        policy_source: currentWorkerStatusData?.policy?.source || null,
        policy_warning_consecutive_failures: currentWorkerStatusData?.policy?.warning_consecutive_failures ?? null,
        policy_critical_consecutive_failures: currentWorkerStatusData?.policy?.critical_consecutive_failures ?? null,
        policy_default_warning_consecutive_failures: currentWorkerStatusData?.policy?.default_warning_consecutive_failures ?? null,
        policy_default_critical_consecutive_failures: currentWorkerStatusData?.policy?.default_critical_consecutive_failures ?? null,
        history_limit: Number.parseInt(historyLimitInput.value, 10),
        policy_audit_limit: Number.parseInt(policyAuditLimitInput.value, 10),
        policy_audit_loaded_rows: policyAuditItems.length,
        policy_audit_actor_user_id: policyAuditActorInput.value.trim() || null,
        policy_audit_request_id: policyAuditRequestInput.value.trim() || null,
      }};
    }}

    function buildPolicyAuditExportFilename(monitorSummary, scopePart = "window") {{
      const workspacePart = sanitizeFilePart(workspaceInput.value, "workspace");
      const scopeNamePart = sanitizeFilePart(scopePart, "window");
      const shiftPart = sanitizeFilePart(
        monitorSummary?.policy_audit_effective_shift_label || monitorSummary?.policy_audit_shift_label,
        "all-shifts",
      );
      const requestPart = sanitizeFilePart(policyAuditRequestInput.value, "all-requests");
      const actorPart = sanitizeFilePart(policyAuditActorInput.value, "all-actors");
      const alertLevelPart = sanitizeFilePart(monitorSummary?.alert_level, "no-alert");
      const currentStatusPart = sanitizeFilePart(monitorSummary?.current_status, "unknown");
      return `${{workspacePart}}-policy-audit-${{scopeNamePart}}-${{shiftPart}}-${{alertLevelPart}}-${{currentStatusPart}}-${{actorPart}}-${{requestPart}}`;
    }}

    function findPolicyAuditItemById(auditId) {{
      return policyAuditItems.find((item) => item.id === auditId) || null;
    }}

    function toCsvValue(value) {{
      if (value === null || value === undefined) {{
        return "";
      }}
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `"${{text.replaceAll('"', '""')}}"`;
    }}

    function buildPolicyAuditCsv(rows, {{ includeMonitorSummary = false }} = {{}}) {{
      const baseHeaders = [
        "id",
        "created_at",
        "workspace_id",
        "action_type",
        "actor_type",
        "actor_user_id",
        "actor_role",
        "session_id",
        "request_id",
        "idempotency_key",
        "subject_type",
        "subject_id",
        "request_warning_consecutive_failures",
        "request_critical_consecutive_failures",
        "result_source",
        "result_warning_consecutive_failures",
        "result_critical_consecutive_failures",
        "request_payload_json",
        "result_payload_json",
      ];
      const summaryHeaders = [
        "summary_exported_at",
        "summary_copy_format",
        "summary_policy_audit_preset_scope",
        "summary_policy_audit_shift_source",
        "summary_policy_audit_shift_label",
        "summary_policy_audit_effective_shift_label",
        "summary_policy_audit_resolved_shift_source",
        "summary_policy_audit_resolved_shift_timezone",
        "summary_policy_audit_resolved_shift_date",
        "summary_policy_audit_resolved_shift_range_start_date",
        "summary_policy_audit_resolved_shift_range_end_date",
        "summary_policy_audit_resolved_shift_note",
        "summary_policy_audit_preset_name",
        "summary_policy_audit_preset_is_default",
        "summary_policy_audit_preset_default_source",
        "summary_monitor_relative_url",
        "summary_monitor_absolute_url",
        "summary_workspace_id",
        "summary_current_status",
        "summary_current_last_seen_at",
        "summary_current_remaining_queued_count",
        "summary_current_consecutive_failures",
        "summary_alert_level",
        "summary_alert_headline",
        "summary_alert_detail",
        "summary_latest_failure_status",
        "summary_latest_failure_at",
        "summary_latest_failure_message",
        "summary_policy_source",
        "summary_policy_warning_consecutive_failures",
        "summary_policy_critical_consecutive_failures",
        "summary_policy_default_warning_consecutive_failures",
        "summary_policy_default_critical_consecutive_failures",
        "summary_history_limit",
        "summary_policy_audit_limit",
        "summary_policy_audit_loaded_rows",
        "summary_policy_audit_actor_user_id",
        "summary_policy_audit_request_id",
      ];
      const headers = includeMonitorSummary ? baseHeaders.concat(summaryHeaders) : baseHeaders;
      const lines = [headers.join(",")];
      rows.forEach((row) => {{
        lines.push(headers.map((header) => toCsvValue(row[header])).join(","));
      }});
      return lines.join("\\n");
    }}

    function exportPolicyAuditItems(format, sourceItems, {{ scopePart = "window", successLabel = "loaded policy audit window" }} = {{}}) {{
      if (!sourceItems || sourceItems.length === 0) {{
        setPolicyAuditActionStatus("No loaded policy audit rows to export.");
        return;
      }}
      try {{
        const includeMonitorSummary = policyAuditIncludeSummaryInput.checked;
        const monitorSummary = buildPolicyAuditMonitorSummary();
        const filenameBase = buildPolicyAuditExportFilename(monitorSummary, scopePart);
        const exportedMonitorSummary = includeMonitorSummary ? monitorSummary : null;
        const rows = buildPolicyAuditExportRows(sourceItems).map((row) => {{
          if (!exportedMonitorSummary) {{
            return row;
          }}
          return {{
            ...row,
            summary_exported_at: exportedMonitorSummary.exported_at,
            summary_copy_format: exportedMonitorSummary.copy_format,
            summary_policy_audit_preset_scope: exportedMonitorSummary.policy_audit_preset_scope,
            summary_policy_audit_shift_source: exportedMonitorSummary.policy_audit_shift_source,
            summary_policy_audit_shift_label: exportedMonitorSummary.policy_audit_shift_label,
            summary_policy_audit_effective_shift_label: exportedMonitorSummary.policy_audit_effective_shift_label,
            summary_policy_audit_resolved_shift_source: exportedMonitorSummary.policy_audit_resolved_shift_source,
            summary_policy_audit_resolved_shift_timezone: exportedMonitorSummary.policy_audit_resolved_shift_timezone,
            summary_policy_audit_resolved_shift_date: exportedMonitorSummary.policy_audit_resolved_shift_date,
            summary_policy_audit_resolved_shift_range_start_date: exportedMonitorSummary.policy_audit_resolved_shift_range_start_date,
            summary_policy_audit_resolved_shift_range_end_date: exportedMonitorSummary.policy_audit_resolved_shift_range_end_date,
            summary_policy_audit_resolved_shift_note: exportedMonitorSummary.policy_audit_resolved_shift_note,
            summary_policy_audit_preset_name: exportedMonitorSummary.policy_audit_preset_name,
            summary_policy_audit_preset_is_default: exportedMonitorSummary.policy_audit_preset_is_default,
            summary_policy_audit_preset_default_source: exportedMonitorSummary.policy_audit_preset_default_source,
            summary_monitor_relative_url: exportedMonitorSummary.monitor_relative_url,
            summary_monitor_absolute_url: exportedMonitorSummary.monitor_absolute_url,
            summary_workspace_id: exportedMonitorSummary.workspace_id,
            summary_current_status: exportedMonitorSummary.current_status,
            summary_current_last_seen_at: exportedMonitorSummary.current_last_seen_at,
            summary_current_remaining_queued_count: exportedMonitorSummary.current_remaining_queued_count,
            summary_current_consecutive_failures: exportedMonitorSummary.current_consecutive_failures,
            summary_alert_level: exportedMonitorSummary.alert_level,
            summary_alert_headline: exportedMonitorSummary.alert_headline,
            summary_alert_detail: exportedMonitorSummary.alert_detail,
            summary_latest_failure_status: exportedMonitorSummary.latest_failure_status,
            summary_latest_failure_at: exportedMonitorSummary.latest_failure_at,
            summary_latest_failure_message: exportedMonitorSummary.latest_failure_message,
            summary_policy_source: exportedMonitorSummary.policy_source,
            summary_policy_warning_consecutive_failures: exportedMonitorSummary.policy_warning_consecutive_failures,
            summary_policy_critical_consecutive_failures: exportedMonitorSummary.policy_critical_consecutive_failures,
            summary_policy_default_warning_consecutive_failures: exportedMonitorSummary.policy_default_warning_consecutive_failures,
            summary_policy_default_critical_consecutive_failures: exportedMonitorSummary.policy_default_critical_consecutive_failures,
            summary_history_limit: exportedMonitorSummary.history_limit,
            summary_policy_audit_limit: exportedMonitorSummary.policy_audit_limit,
            summary_policy_audit_loaded_rows: exportedMonitorSummary.policy_audit_loaded_rows,
            summary_policy_audit_actor_user_id: exportedMonitorSummary.policy_audit_actor_user_id,
            summary_policy_audit_request_id: exportedMonitorSummary.policy_audit_request_id,
          }};
        }});
        if (format === "json") {{
          const payload = {{
            workspace_id: workspaceInput.value.trim() || null,
            export_scope: scopePart,
            filters: {{
              actor_user_id: policyAuditActorInput.value.trim() || null,
              request_id: policyAuditRequestInput.value.trim() || null,
              limit: Number.parseInt(policyAuditLimitInput.value, 10),
              loaded_rows: sourceItems.length,
            }},
            monitor_summary: exportedMonitorSummary,
            rows,
          }};
          downloadTextFile(`${{filenameBase}}.json`, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
          setPolicyAuditActionStatus(
            includeMonitorSummary
              ? `Exported ${{successLabel}} as JSON with monitor summary.`
              : `Exported ${{successLabel}} as JSON.`,
            "success",
          );
          return;
        }}
        downloadTextFile(
          `${{filenameBase}}.csv`,
          buildPolicyAuditCsv(rows, {{ includeMonitorSummary }}),
          "text/csv;charset=utf-8",
        );
        setPolicyAuditActionStatus(
          includeMonitorSummary
            ? `Exported ${{successLabel}} as CSV with monitor summary.`
            : `Exported ${{successLabel}} as CSV.`,
          "success",
        );
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    function exportPolicyAuditWindow(format) {{
      exportPolicyAuditItems(format, policyAuditItems, {{
        scopePart: "window",
        successLabel: "loaded policy audit window",
      }});
    }}

    function exportLatestPolicyAudit(format) {{
      if (policyAuditItems.length === 0) {{
        setPolicyAuditActionStatus("No latest policy audit row is available to export.");
        return;
      }}
      exportPolicyAuditItems(format, [policyAuditItems[0]], {{
        scopePart: "latest",
        successLabel: "latest policy audit row",
      }});
    }}

    function exportSinglePolicyAudit(auditId, format) {{
      const item = findPolicyAuditItemById(auditId);
      if (!item) {{
        setPolicyAuditActionStatus("Selected policy audit row is no longer loaded.");
        return;
      }}
      const scopeSuffix = sanitizeFilePart(item.request_id || item.id || "row", "row");
      exportPolicyAuditItems(format, [item], {{
        scopePart: `row-${{scopeSuffix}}`,
        successLabel: "selected policy audit row",
      }});
    }}

    async function copyPolicyAuditRequest(requestId) {{
      if (!requestId) {{
        setPolicyAuditActionStatus("This audit row does not have a request_id to copy.");
        return;
      }}
      try {{
        await copyTextToClipboard(requestId);
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(`Copied request_id ${{requestId}}.`, "success");
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
    }}

    function applyPolicyAuditFiltersFromRow(actorUserId, requestId) {{
      policyAuditActorInput.value = actorUserId || "";
      policyAuditRequestInput.value = requestId || "";
      setPolicyAuditActionStatus(
        requestId
          ? `Applied audit filters for request_id ${{requestId}}.`
          : "Applied audit filters from selected row.",
        "success",
      );
      render();
    }}

    function togglePolicyAuditDetails(auditId) {{
      if (!auditId) {{
        return;
      }}
      if (policyAuditExpandedIds.has(auditId)) {{
        policyAuditExpandedIds.delete(auditId);
        setPolicyAuditActionStatus("Collapsed payload details.");
      }} else {{
        policyAuditExpandedIds.add(auditId);
        setPolicyAuditActionStatus("Expanded payload details.", "success");
      }}
      renderPolicyAuditLogs({{ items: policyAuditItems, hasMore: policyAuditHasMore }});
    }}

    function resetPolicyAuditState() {{
      policyAuditCursor = null;
      policyAuditHasMore = false;
      policyAuditItems = [];
      policyAuditWindowExpanded = false;
    }}

    async function refreshWorkerStatus() {{
      updateQueryString();
      const workspaceId = workspaceInput.value.trim();
      const historyLimit = historyLimitInput.value;
      const params = new URLSearchParams();
      if (workspaceId) {{
        params.set("workspace_id", workspaceId);
      }}
      params.set("history_limit", historyLimit);
      const response = await fetch(`/api/v1/opsgraph/replays/worker-status?${{params.toString()}}`, {{
        headers: {{ "Accept": "application/json" }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    async function patchWorkerPolicy(warningThreshold, criticalThreshold) {{
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        throw new Error("workspace_id is required to update replay worker alert policy.");
      }}
      const params = new URLSearchParams({{ workspace_id: workspaceId }});
      const response = await fetch(`/api/v1/opsgraph/replays/worker-alert-policy?${{params.toString()}}`, {{
        method: "PATCH",
        headers: {{
          "Accept": "application/json",
          "Content-Type": "application/json",
        }},
        credentials: "same-origin",
        body: JSON.stringify({{
          warning_consecutive_failures: warningThreshold,
          critical_consecutive_failures: criticalThreshold,
        }}),
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      return payload.data;
    }}

    async function refreshPolicyAuditLogs({{ append = false }} = {{}}) {{
      updateQueryString();
      const workspaceId = workspaceInput.value.trim();
      if (!workspaceId) {{
        resetPolicyAuditState();
        return {{ items: [], hasMore: false, nextCursor: null }};
      }}
      const params = new URLSearchParams({{
        workspace_id: workspaceId,
        action_type: "replay.update_worker_alert_policy",
        limit: policyAuditLimitInput.value,
      }});
      const actorUserId = policyAuditActorInput.value.trim();
      const requestId = policyAuditRequestInput.value.trim();
      if (actorUserId) {{
        params.set("actor_user_id", actorUserId);
      }}
      if (requestId) {{
        params.set("request_id", requestId);
      }}
      if (append && policyAuditCursor) {{
        params.set("cursor", policyAuditCursor);
      }}
      const response = await fetch(`/api/v1/opsgraph/replays/audit-logs?${{params.toString()}}`, {{
        headers: {{ "Accept": "application/json" }},
        credentials: "same-origin",
      }});
      if (!response.ok) {{
        const payload = await response.json().catch(() => ({{ error: {{ message: response.statusText }} }}));
        throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      const pageItems = Array.isArray(payload.data) ? payload.data : [];
      policyAuditItems = append ? policyAuditItems.concat(pageItems) : pageItems;
      policyAuditHasMore = Boolean(payload.meta?.has_more);
      policyAuditCursor = payload.meta?.next_cursor || null;
      policyAuditWindowExpanded = append ? true : false;
      return {{
        items: policyAuditItems,
        hasMore: policyAuditHasMore,
        nextCursor: policyAuditCursor,
      }};
    }}

    function renderHistoryRows(items) {{
      if (!items || items.length === 0) {{
        historyBody.innerHTML = '<tr><td colspan="8" class="mono">No heartbeat history available.</td></tr>';
        return;
      }}
      historyBody.innerHTML = items.map((item) => `
        <tr>
          <td><span class="pill ${{String(item.status || '').toLowerCase()}}">${{item.status || 'unknown'}}</span></td>
          <td class="mono">${{item.iteration ?? '-'}}</td>
          <td class="mono">${{item.attempted_count ?? '-'}}</td>
          <td class="mono">${{item.dispatched_count ?? '-'}}</td>
          <td class="mono">${{item.failed_count ?? '-'}}</td>
          <td class="mono">${{item.remaining_queued_count ?? '-'}}</td>
          <td class="mono">${{item.emitted_at || '-'}}</td>
          <td>${{item.error_message || ''}}</td>
        </tr>
      `).join("");
    }}

    function renderPolicyAuditLogs(state, {{ highlightFresh = false }} = {{}}) {{
      const items = state?.items || [];
      const hasMore = Boolean(state?.hasMore);
      const nextSeenIds = new Set(
        items
          .map((item) => item.id || "")
          .filter((itemId) => itemId !== ""),
      );
      if (highlightFresh && policyAuditSeenIds.size > 0) {{
        items.forEach((item) => {{
          if (item.id && !policyAuditSeenIds.has(item.id)) {{
            policyAuditFreshIds.add(item.id);
          }}
        }});
      }}
      policyAuditFreshIds = new Set(
        Array.from(policyAuditFreshIds).filter((itemId) => nextSeenIds.has(itemId)),
      );
      policyAuditExpandedIds = new Set(
        Array.from(policyAuditExpandedIds).filter((itemId) => nextSeenIds.has(itemId)),
      );
      policyAuditSeenIds = nextSeenIds;
      const filters = [];
      if (policyAuditActorInput.value.trim()) {{
        filters.push(`actor=${{policyAuditActorInput.value.trim()}}`);
      }}
      if (policyAuditRequestInput.value.trim()) {{
        filters.push(`request=${{policyAuditRequestInput.value.trim()}}`);
      }}
      const metaParts = [
        `rows=${{items.length}}`,
        `page=${{policyAuditLimitInput.value}}`,
        `more=${{hasMore ? "yes" : "no"}}`,
      ];
      if (filters.length > 0) {{
        metaParts.push(...filters);
      }}
      loadOlderPolicyAuditButton.disabled = !hasMore;
      resetPolicyAuditWindowButton.disabled = !policyAuditWindowExpanded;
      if (!items || items.length === 0) {{
        policyAuditMeta.textContent = metaParts.join(" | ");
        policyAuditBody.innerHTML = '<tr><td colspan="6" class="mono">No replay worker policy changes recorded.</td></tr>';
        return;
      }}
      policyAuditMeta.textContent = metaParts.join(" | ");
      policyAuditBody.innerHTML = items.map((item) => {{
        const isFresh = Boolean(item.id && policyAuditFreshIds.has(item.id));
        const isExpanded = Boolean(item.id && policyAuditExpandedIds.has(item.id));
        const actorLabel = item.actor_user_id
          ? `${{item.actor_user_id}} (${{item.actor_role || item.actor_type || 'user'}})`
          : (item.actor_type || "system");
        const requestThresholds = `${{item.request_payload?.warning_consecutive_failures ?? '-'}} / ${{item.request_payload?.critical_consecutive_failures ?? '-'}}`;
        const resultSource = item.result_payload?.source || "-";
        const resultThresholds = `${{item.result_payload?.warning_consecutive_failures ?? '-'}} / ${{item.result_payload?.critical_consecutive_failures ?? '-'}}`;
        const actorValue = encodeURIComponent(item.actor_user_id || "");
        const requestValue = encodeURIComponent(item.request_id || "");
        const auditIdValue = encodeURIComponent(item.id || "");
        return `
          <tr class="${{isFresh ? "policy-audit-fresh" : ""}}">
            <td class="mono">${{item.created_at || '-'}}</td>
            <td>${{actorLabel}}</td>
            <td class="mono">${{requestThresholds}}</td>
            <td>${{resultSource}} (${{resultThresholds}})</td>
            <td class="mono">${{item.request_id || '-'}}${{isFresh ? '<span class="fresh-flag">New</span>' : ''}}</td>
            <td>
              <div class="audit-row-actions">
                <button type="button" data-policy-audit-action="copy-request" data-request="${{requestValue}}" ${{item.request_id ? "" : "disabled"}}>Copy Request</button>
                <button type="button" data-policy-audit-action="copy-row-context" data-audit-id="${{auditIdValue}}">Row Context</button>
                <button type="button" data-policy-audit-action="use-filters" data-actor="${{actorValue}}" data-request="${{requestValue}}">Use Filters</button>
                <button type="button" data-policy-audit-action="export-row-json" data-audit-id="${{auditIdValue}}">Row JSON</button>
                <button type="button" data-policy-audit-action="export-row-csv" data-audit-id="${{auditIdValue}}">Row CSV</button>
                <button type="button" data-policy-audit-action="toggle-details" data-audit-id="${{auditIdValue}}">${{isExpanded ? "Hide Payload" : "Show Payload"}}</button>
              </div>
            </td>
          </tr>
          <tr class="audit-detail-row" ${{isExpanded ? "" : "hidden"}}>
            <td colspan="6">
              <div class="audit-detail-grid">
                <section class="audit-detail-panel">
                  <h3>Request Payload</h3>
                  ${{formatAuditPayload(item.request_payload)}}
                </section>
                <section class="audit-detail-panel">
                  <h3>Result Payload</h3>
                  ${{formatAuditPayload(item.result_payload)}}
                </section>
              </div>
            </td>
          </tr>
        `;
      }}).join("");
    }}

    function renderWorkerStatus(data) {{
      currentWorkerStatusData = data;
      const current = data.current || {{}};
      applyStatusPill(currentStatus, current.status || "No heartbeat");
      remainingQueued.textContent = String(current.remaining_queued_count ?? "-");
      lastSeen.textContent = current.last_seen_at || "-";
      consecutiveFailures.textContent = String(current.consecutive_failures ?? 0);
      historyMeta.textContent = `workspace=${{data.workspace_id || workspaceInput.value.trim() || 'n/a'}} | rows=${{(data.history || []).length}}`;
      applyAlert(data.alert || null);
      renderPolicy(data.policy || null);
      renderHistoryRows(data.history || []);
    }}

    async function loadOlderPolicyAuditLogs() {{
      if (!policyAuditHasMore) {{
        return;
      }}
      try {{
        loadOlderPolicyAuditButton.disabled = true;
        const policyAuditState = await refreshPolicyAuditLogs({{ append: true }});
        errorBox.style.display = "none";
        renderPolicyAuditLogs(policyAuditState);
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
      }}
    }}

    async function render({{ highlightFresh = false }} = {{}}) {{
      try {{
        resetPolicyAuditState();
        const [data, policyAuditState] = await Promise.all([
          refreshWorkerStatus(),
          refreshPolicyAuditLogs(),
        ]);
        errorBox.style.display = "none";
        renderWorkerStatus(data);
        renderPolicyAuditLogs(policyAuditState, {{ highlightFresh }});
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
      }}
    }}

    async function savePolicy({{ reset = false }} = {{}}) {{
      try {{
        const warningThreshold = reset
          ? currentPolicy?.defaultWarning
          : Number.parseInt(warningThresholdInput.value, 10);
        const criticalThreshold = reset
          ? currentPolicy?.defaultCritical
          : Number.parseInt(criticalThresholdInput.value, 10);
        if (!Number.isInteger(warningThreshold) || warningThreshold < 1) {{
          throw new Error("warning threshold must be an integer greater than or equal to 1.");
        }}
        if (!Number.isInteger(criticalThreshold) || criticalThreshold < warningThreshold) {{
          throw new Error("critical threshold must be an integer greater than or equal to the warning threshold.");
        }}
        savePolicyButton.disabled = true;
        resetPolicyButton.disabled = true;
        setPolicyStatus(reset ? "Resetting workspace policy..." : "Saving workspace policy...");
        const policy = await patchWorkerPolicy(warningThreshold, criticalThreshold);
        renderPolicy(policy);
        setPolicyStatus(
          policy.source === "default"
            ? "Workspace policy reset to runtime default."
            : "Workspace policy override saved.",
          "success",
        );
        await render({{ highlightFresh: true }});
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyStatus(error.message || String(error), "error");
      }} finally {{
        savePolicyButton.disabled = false;
        if (currentPolicy !== null) {{
          resetPolicyButton.disabled = currentPolicy.source !== "workspace_override";
        }}
      }}
    }}

    function buildStreamUrl() {{
      const params = new URLSearchParams();
      const workspaceId = workspaceInput.value.trim();
      if (workspaceId) {{
        params.set("workspace_id", workspaceId);
      }}
      params.set("history_limit", historyLimitInput.value);
      return `/api/v1/opsgraph/replays/worker-status/stream?${{params.toString()}}`;
    }}

    function closeStream() {{
      if (liveSource !== null) {{
        liveSource.close();
        liveSource = null;
      }}
    }}

    function connectStream() {{
      closeStream();
      if (!liveUpdates) {{
        refreshMode.textContent = "Manual refresh only";
        toggleAutoButton.textContent = "Resume Live Stream";
        return;
      }}
      refreshMode.textContent = "Connecting live stream...";
      toggleAutoButton.textContent = "Pause Live Stream";
      liveSource = new EventSource(buildStreamUrl(), {{ withCredentials: true }});
      liveSource.addEventListener("opsgraph.replay_worker.status", (event) => {{
        try {{
          const data = JSON.parse(event.data);
          errorBox.style.display = "none";
          renderWorkerStatus(data);
          refreshMode.textContent = "Live via SSE";
        }} catch (error) {{
          errorBox.textContent = error.message || String(error);
          errorBox.style.display = "block";
        }}
      }});
      liveSource.onerror = () => {{
        refreshMode.textContent = "SSE reconnecting...";
      }};
    }}

    async function initializePolicyAuditPresets() {{
      policyAuditBrowserPresets = loadStoredBrowserPolicyAuditPresets();
      syncActivePolicyAuditPresets();
      if (getPolicyAuditPresetScope() === "workspace") {{
        await refreshWorkspacePolicyAuditPresets();
      }} else {{
        await refreshPolicyAuditShiftResolution();
      }}
      applyInitialPolicyAuditPresetSelection();
    }}

    refreshButton.addEventListener("click", () => render({{ highlightFresh: true }}));
    workspaceInput.addEventListener("change", async () => {{
      setPolicyStatus("Loading workspace policy...");
      await refreshShiftScheduleEditor();
      try {{
        if (getPolicyAuditPresetScope() === "workspace") {{
          await refreshWorkspacePolicyAuditPresets();
          applyInitialPolicyAuditPresetSelection();
        }} else {{
          await refreshPolicyAuditShiftResolution();
          renderPolicyAuditPresetOptions();
        }}
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
      await render();
      connectStream();
    }});
    historyLimitInput.addEventListener("change", () => {{
      render();
      connectStream();
    }});
    [shiftScheduleTimezoneInput, shiftScheduleWindowsInput, shiftScheduleDateOverridesInput, shiftScheduleDateRangeOverridesInput].forEach((input) => {{
      input.addEventListener("change", syncShiftScheduleDraftFromEditors);
    }});
    addShiftScheduleBaseWindowButton.addEventListener("click", addShiftScheduleBaseWindow);
    addShiftScheduleDateOverrideButton.addEventListener("click", addShiftScheduleDateOverrideWindow);
    addShiftScheduleRangeOverrideButton.addEventListener("click", addShiftScheduleRangeOverrideWindow);
    applyPolicyAuditFiltersButton.addEventListener("click", render);
    clearPolicyAuditFiltersButton.addEventListener("click", () => {{
      policyAuditActorInput.value = "";
      policyAuditRequestInput.value = "";
      setPolicyAuditActionStatus("Cleared audit filters.");
      render();
    }});
    copyPolicyAuditLinkButton.addEventListener("click", copyCurrentPolicyAuditLink);
    copyLatestPolicyAuditContextButton.addEventListener("click", copyLatestPolicyAuditContext);
    policyAuditPresetScopeInput.addEventListener("change", async () => {{
      updateQueryString();
      if (getPolicyAuditPresetScope() === "workspace") {{
        try {{
          await refreshWorkspacePolicyAuditPresets();
          applyInitialPolicyAuditPresetSelection();
          setPolicyAuditActionStatus("Switched preset scope to workspace.", "success");
        }} catch (error) {{
          errorBox.textContent = error.message || String(error);
          errorBox.style.display = "block";
          setPolicyAuditActionStatus(error.message || String(error), "error");
        }}
      }} else {{
        await refreshPolicyAuditShiftResolution();
        applyInitialPolicyAuditPresetSelection();
        errorBox.style.display = "none";
        setPolicyAuditActionStatus("Switched preset scope to browser.", "success");
      }}
    }});
    policyAuditShiftSourceInput.addEventListener("change", async () => {{
      updateQueryString();
      try {{
        if (getPolicyAuditPresetScope() === "workspace") {{
          await refreshWorkspacePolicyAuditPresets();
          applyInitialPolicyAuditPresetSelection();
        }} else {{
          await refreshPolicyAuditShiftResolution();
          syncPolicyAuditPresetControls();
        }}
        errorBox.style.display = "none";
        setPolicyAuditActionStatus(
          getPolicyAuditShiftSource() === "auto"
            ? "Auto shift resolution is active."
            : "Manual shift selection is active.",
          "success",
        );
      }} catch (error) {{
        errorBox.textContent = error.message || String(error);
        errorBox.style.display = "block";
        setPolicyAuditActionStatus(error.message || String(error), "error");
      }}
      await render();
    }});
    policyAuditShiftLabelInput.addEventListener("change", async () => {{
      updateQueryString();
      if (getPolicyAuditShiftSource() === "manual" && getPolicyAuditPresetScope() === "workspace") {{
        try {{
          await refreshWorkspacePolicyAuditPresets();
          applyInitialPolicyAuditPresetSelection();
          errorBox.style.display = "none";
          setPolicyAuditActionStatus(
            getPolicyAuditShiftLabel()
              ? `Applied workspace presets for shift ${{getPolicyAuditShiftLabel()}}.`
              : "Cleared shift label and restored workspace-wide preset defaults.",
            "success",
          );
        }} catch (error) {{
          errorBox.textContent = error.message || String(error);
          errorBox.style.display = "block";
          setPolicyAuditActionStatus(error.message || String(error), "error");
        }}
      }} else {{
        renderPolicyAuditShiftMeta();
        syncPolicyAuditPresetControls();
      }}
      await render();
    }});
    policyAuditPresetNameInput.addEventListener("input", syncPolicyAuditPresetControls);
    policyAuditPresetSelect.addEventListener("change", () => {{
      if (policyAuditPresetSelect.value) {{
        policyAuditPresetNameInput.value = policyAuditPresetSelect.value;
      }}
      syncPolicyAuditPresetControls();
    }});
    savePolicyAuditPresetButton.addEventListener("click", saveCurrentPolicyAuditPreset);
    loadPolicyAuditPresetButton.addEventListener("click", loadSelectedPolicyAuditPreset);
    deletePolicyAuditPresetButton.addEventListener("click", deleteSelectedPolicyAuditPreset);
    setPolicyAuditDefaultPresetButton.addEventListener("click", setSelectedPolicyAuditDefaultPreset);
    clearPolicyAuditDefaultPresetButton.addEventListener("click", clearSelectedPolicyAuditDefaultPreset);
    exportPolicyAuditJsonButton.addEventListener("click", () => exportPolicyAuditWindow("json"));
    exportPolicyAuditCsvButton.addEventListener("click", () => exportPolicyAuditWindow("csv"));
    exportLatestPolicyAuditJsonButton.addEventListener("click", () => exportLatestPolicyAudit("json"));
    exportLatestPolicyAuditCsvButton.addEventListener("click", () => exportLatestPolicyAudit("csv"));
    policyAuditCopyFormatInput.addEventListener("change", updateQueryString);
    policyAuditIncludeSummaryInput.addEventListener("change", updateQueryString);
    policyAuditLimitInput.addEventListener("change", render);
    loadOlderPolicyAuditButton.addEventListener("click", loadOlderPolicyAuditLogs);
    resetPolicyAuditWindowButton.addEventListener("click", render);
    loadShiftScheduleButton.addEventListener("click", refreshShiftScheduleEditor);
    saveShiftScheduleButton.addEventListener("click", saveShiftSchedule);
    clearShiftScheduleButton.addEventListener("click", clearShiftSchedule);
    copyShiftScheduleJsonButton.addEventListener("click", copyShiftScheduleJson);
    exportShiftScheduleJsonButton.addEventListener("click", exportShiftScheduleJson);
    importShiftScheduleJsonButton.addEventListener("click", promptShiftScheduleJsonImport);
    shiftScheduleImportInput.addEventListener("change", importShiftScheduleJson);
    applyShiftScheduleImportButton.addEventListener("click", applyShiftScheduleImportPreview);
    discardShiftScheduleImportButton.addEventListener("click", discardShiftScheduleImportPreview);
    [shiftScheduleWindowsBody, shiftScheduleDateOverridesBody, shiftScheduleDateRangeOverridesBody].forEach((body) => {{
      body.addEventListener("click", (event) => {{
        const actionButton = event.target.closest("button[data-shift-schedule-action]");
        if (!actionButton) {{
          return;
        }}
        const action = actionButton.dataset.shiftScheduleAction || "";
        const overrideIndex = Number.parseInt(actionButton.dataset.overrideIndex || "-1", 10);
        const windowIndex = Number.parseInt(actionButton.dataset.windowIndex || "-1", 10);
        if (action === "edit-base-window" && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          editShiftScheduleBaseWindow(windowIndex);
          return;
        }}
        if (action === "move-base-window-up" && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleBaseWindow(windowIndex, "up");
          return;
        }}
        if (action === "move-base-window-down" && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleBaseWindow(windowIndex, "down");
          return;
        }}
        if (action === "remove-base-window" && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          removeShiftScheduleBaseWindow(windowIndex);
          return;
        }}
        if (action === "edit-date-override-window" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          editShiftScheduleDateOverrideWindow(overrideIndex, windowIndex);
          return;
        }}
        if (action === "move-date-override-window-up" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleDateOverrideWindow(overrideIndex, windowIndex, "up");
          return;
        }}
        if (action === "move-date-override-window-down" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleDateOverrideWindow(overrideIndex, windowIndex, "down");
          return;
        }}
        if (action === "remove-date-override-window" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          removeShiftScheduleDateOverrideWindow(overrideIndex, windowIndex);
          return;
        }}
        if (action === "edit-range-override-window" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          editShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex);
          return;
        }}
        if (action === "move-range-override-window-up" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex, "up");
          return;
        }}
        if (action === "move-range-override-window-down" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          moveShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex, "down");
          return;
        }}
        if (action === "remove-range-override-window" && Number.isInteger(overrideIndex) && overrideIndex >= 0 && Number.isInteger(windowIndex) && windowIndex >= 0) {{
          removeShiftScheduleRangeOverrideWindow(overrideIndex, windowIndex);
        }}
      }});
    }});
    policyAuditBody.addEventListener("click", (event) => {{
      const actionButton = event.target.closest("button[data-policy-audit-action]");
      if (!actionButton) {{
        return;
      }}
      const actorUserId = decodeURIComponent(actionButton.dataset.actor || "");
      const requestId = decodeURIComponent(actionButton.dataset.request || "");
      const auditId = decodeURIComponent(actionButton.dataset.auditId || "");
      if (actionButton.dataset.policyAuditAction === "copy-request") {{
        copyPolicyAuditRequest(requestId);
        return;
      }}
      if (actionButton.dataset.policyAuditAction === "copy-row-context") {{
        copySinglePolicyAuditContext(auditId);
        return;
      }}
      if (actionButton.dataset.policyAuditAction === "use-filters") {{
        applyPolicyAuditFiltersFromRow(actorUserId, requestId);
        return;
      }}
      if (actionButton.dataset.policyAuditAction === "export-row-json") {{
        exportSinglePolicyAudit(auditId, "json");
        return;
      }}
      if (actionButton.dataset.policyAuditAction === "export-row-csv") {{
        exportSinglePolicyAudit(auditId, "csv");
        return;
      }}
      if (actionButton.dataset.policyAuditAction === "toggle-details") {{
        togglePolicyAuditDetails(auditId);
      }}
    }});
    [policyAuditActorInput, policyAuditRequestInput].forEach((input) => {{
      input.addEventListener("keydown", (event) => {{
        if (event.key === "Enter") {{
          event.preventDefault();
          render();
        }}
      }});
    }});
    savePolicyButton.addEventListener("click", () => savePolicy());
    resetPolicyButton.addEventListener("click", () => savePolicy({{ reset: true }}));
    toggleAutoButton.addEventListener("click", () => {{
      liveUpdates = !liveUpdates;
      connectStream();
    }});

    async function initializeReplayWorkerMonitor() {{
      clearShiftScheduleImportPreview();
      await initializePolicyAuditPresets();
      await refreshShiftScheduleEditor();
      connectStream();
      await render();
    }}

    initializeReplayWorkerMonitor();
  </script>
</body>
</html>"""


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


def create_fastapi_app(service: OpsGraphAppService, *, route_authorizer=None):
    ap = load_shared_agent_platform()
    try:
        from fastapi import Cookie, Depends, FastAPI, Header, Query, Request, Response
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
        return HTMLResponse(_render_replay_worker_monitor_html())

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
