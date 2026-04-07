from __future__ import annotations

from typing import Any

from .api_models import (
    HealthResponse,
    HealthRuntimeSummary,
    RemoteProviderSmokeAlertItem,
    RemoteProviderSmokeAlertSummary,
    RemoteProviderSmokeCommand,
    RemoteProviderSmokeHistorySummary,
    RemoteProviderSmokeProviderSummary,
    RemoteProviderSmokeResponse,
    RemoteProviderSmokeRunRecord,
    RuntimeCapabilitiesResponse,
    RuntimeProviderAlertSummary,
)
from .remote_provider_smoke import run_remote_provider_smoke_suite
from .tool_adapters import describe_opsgraph_product_tool_capabilities


def runtime_provider_is_remote_active(effective_mode: str | None) -> bool:
    normalized = str(effective_mode or "").strip().lower()
    return normalized not in {"", "local", "unavailable", "unknown"}


def build_runtime_provider_alert_summary(
    *,
    model_provider: dict[str, Any],
    tooling: dict[str, dict[str, Any]],
) -> RuntimeProviderAlertSummary:
    alerts: list[dict[str, Any]] = []
    capabilities = [("model_provider", model_provider)] + sorted(tooling.items())
    for capability_name, descriptor in capabilities:
        details = descriptor.get("details") if isinstance(descriptor.get("details"), dict) else {}
        strict_remote_required = bool(details.get("strict_remote_required"))
        effective_mode = str(descriptor.get("effective_mode") or "unknown")
        requested_mode = str(descriptor.get("requested_mode") or "unknown")
        backend_id = str(descriptor.get("backend_id") or "unknown")
        fallback_reason = (
            str(descriptor.get("fallback_reason"))
            if descriptor.get("fallback_reason") is not None
            else None
        )
        last_remote_error = (
            str(details.get("last_remote_error"))
            if details.get("last_remote_error") is not None
            else None
        )
        last_primary_error = (
            str(details.get("last_primary_error"))
            if details.get("last_primary_error") is not None
            else None
        )
        last_error = last_remote_error or last_primary_error
        if strict_remote_required and not runtime_provider_is_remote_active(effective_mode):
            alerts.append(
                {
                    "capability_name": capability_name,
                    "level": "critical",
                    "requested_mode": requested_mode,
                    "effective_mode": effective_mode,
                    "backend_id": backend_id,
                    "strict_remote_required": True,
                    "reason_code": fallback_reason or "STRICT_REMOTE_PROVIDER_UNAVAILABLE",
                    "detail": (
                        f"{capability_name} requires a remote backend but is currently operating "
                        f"in {effective_mode} mode."
                    ),
                }
            )
            continue
        if last_error:
            alerts.append(
                {
                    "capability_name": capability_name,
                    "level": "warning",
                    "requested_mode": requested_mode,
                    "effective_mode": effective_mode,
                    "backend_id": backend_id,
                    "strict_remote_required": strict_remote_required,
                    "reason_code": last_error,
                    "detail": (
                        f"{capability_name} recorded a recent remote error and may be serving "
                        "fallback output."
                    ),
                }
            )
    if not alerts:
        return RuntimeProviderAlertSummary(
            level="healthy",
            headline="No active runtime provider alerts",
            detail="No strict remote-provider failures or recent remote errors detected.",
            active_alert_count=0,
            alerts=[],
        )
    alerts.sort(
        key=lambda item: (
            0 if item["level"] == "critical" else 1,
            str(item["capability_name"]),
        )
    )
    critical_count = sum(1 for item in alerts if item["level"] == "critical")
    warning_count = sum(1 for item in alerts if item["level"] == "warning")
    affected = ", ".join(item["capability_name"] for item in alerts[:3])
    if len(alerts) > 3:
        affected = f"{affected}, ..."
    detail = f"{critical_count} critical and {warning_count} warning runtime provider alerts."
    if affected:
        detail = f"{detail} Affected: {affected}."
    return RuntimeProviderAlertSummary(
        level="critical" if critical_count else "warning",
        headline="Runtime provider alerts active",
        detail=detail,
        active_alert_count=len(alerts),
        alerts=alerts,
    )


def build_remote_provider_smoke_alert_summary(
    service,
    *,
    smoke_summary: RemoteProviderSmokeHistorySummary,
) -> RemoteProviderSmokeAlertSummary:
    alerts: list[dict[str, Any]] = []
    for provider_summary in smoke_summary.providers:
        level: str | None = None
        reason_code: str | None = None
        detail: str | None = None
        if (
            provider_summary.latest_strict_remote_required
            and provider_summary.last_status is not None
            and provider_summary.last_status != "success"
        ):
            level = "critical"
            reason_code = provider_summary.last_reason or "STRICT_REMOTE_PROVIDER_SMOKE_NOT_SUCCESS"
            detail = (
                f"{provider_summary.provider} is marked strict-remote and its latest smoke run "
                f"finished with status {provider_summary.last_status}."
            )
        elif (
            provider_summary.consecutive_failure_count
            >= service.remote_provider_smoke_alert_critical_consecutive_failures
        ):
            level = "critical"
            reason_code = provider_summary.last_reason or "REMOTE_PROVIDER_SMOKE_CONSECUTIVE_FAILURES"
            detail = (
                f"{provider_summary.provider} failed "
                f"{provider_summary.consecutive_failure_count} consecutive smoke runs."
            )
        elif (
            provider_summary.consecutive_failure_count
            >= service.remote_provider_smoke_alert_warning_consecutive_failures
        ):
            level = "warning"
            reason_code = provider_summary.last_reason or "REMOTE_PROVIDER_SMOKE_CONSECUTIVE_FAILURES"
            detail = (
                f"{provider_summary.provider} failed "
                f"{provider_summary.consecutive_failure_count} consecutive smoke runs."
            )
        if level is None or reason_code is None or detail is None:
            continue
        alerts.append(
            {
                "provider": provider_summary.provider,
                "level": level,
                "reason_code": reason_code,
                "detail": detail,
                "last_status": provider_summary.last_status,
                "last_reason": provider_summary.last_reason,
                "last_seen_at": provider_summary.last_seen_at,
                "last_diagnostic_run_id": provider_summary.last_diagnostic_run_id,
                "consecutive_failure_count": provider_summary.consecutive_failure_count,
                "consecutive_non_success_count": provider_summary.consecutive_non_success_count,
            }
        )
    if not alerts:
        return RemoteProviderSmokeAlertSummary(
            level="healthy",
            headline="No active smoke-history alerts",
            detail="No providers have crossed the configured smoke regression thresholds.",
            active_alert_count=0,
            alerts=[],
        )
    alerts.sort(
        key=lambda item: (
            0 if item["level"] == "critical" else 1,
            str(item["provider"]),
        )
    )
    critical_count = sum(1 for item in alerts if item["level"] == "critical")
    warning_count = sum(1 for item in alerts if item["level"] == "warning")
    affected = ", ".join(item["provider"] for item in alerts[:3])
    if len(alerts) > 3:
        affected = f"{affected}, ..."
    detail = f"{critical_count} critical and {warning_count} warning smoke-history alerts."
    if affected:
        detail = f"{detail} Affected: {affected}."
    return RemoteProviderSmokeAlertSummary(
        level="critical" if critical_count else "warning",
        headline="Remote provider smoke alerts active",
        detail=detail,
        active_alert_count=len(alerts),
        alerts=[RemoteProviderSmokeAlertItem.model_validate(item) for item in alerts],
    )


def get_runtime_capabilities(service) -> RuntimeCapabilitiesResponse:
    model_gateway = getattr(service.workflow_api_service.execution_service, "model_gateway", None)
    tool_executor = getattr(service.workflow_api_service.execution_service, "tool_executor", None)
    auth_service = getattr(service, "auth_service", None)
    replay_worker_status = service.repository.get_replay_worker_status()
    replay_worker_history = service.repository.list_replay_worker_history(limit=5)
    replay_worker_workspace_id = (
        replay_worker_status.workspace_id
        if replay_worker_status is not None
        else (replay_worker_history[0].workspace_id if replay_worker_history else None)
    )
    replay_worker_policy = service._resolve_replay_worker_alert_policy(replay_worker_workspace_id)
    replay_worker_alert = service._build_replay_worker_alert(
        current=replay_worker_status,
        latest_failure=service._latest_replay_worker_failure(replay_worker_history),
        policy=replay_worker_policy,
    )
    model_provider = (
        model_gateway.describe_capability()
        if model_gateway is not None and hasattr(model_gateway, "describe_capability")
        else {
            "requested_mode": "unknown",
            "effective_mode": "unknown",
            "backend_id": "unknown",
            "fallback_reason": None,
            "details": {},
        }
    )
    tooling = describe_opsgraph_product_tool_capabilities(tool_executor)
    runtime_provider_alert = build_runtime_provider_alert_summary(
        model_provider=model_provider,
        tooling=tooling,
    )
    remote_provider_smoke_alert = build_remote_provider_smoke_alert_summary(
        service,
        smoke_summary=summarize_remote_provider_smoke_runs(service, limit=50),
    )
    auth_summary = (
        auth_service.describe_runtime_auth_mode()
        if auth_service is not None and hasattr(auth_service, "describe_runtime_auth_mode")
        else None
    )
    return RuntimeCapabilitiesResponse.model_validate(
        {
            "product": "opsgraph",
            "model_provider": model_provider,
            "tooling": tooling,
            "auth": auth_summary,
            "runtime_provider_alert": runtime_provider_alert.model_dump(mode="json"),
            "remote_provider_smoke_alert": remote_provider_smoke_alert.model_dump(mode="json"),
            "replay_worker": (
                replay_worker_status.model_dump(mode="json")
                if replay_worker_status is not None
                else None
            ),
            "replay_worker_history": [
                item.model_dump(mode="json") for item in replay_worker_history
            ],
            "replay_worker_alert": (
                replay_worker_alert.model_dump(mode="json")
                if replay_worker_alert is not None
                else None
            ),
            "replay_worker_alert_policy": replay_worker_policy.model_dump(mode="json"),
        }
    )


def run_remote_provider_smoke(
    service,
    command,
    *,
    auth_context=None,
    request_id: str | None = None,
) -> RemoteProviderSmokeResponse:
    request = service._coerce_command(command, RemoteProviderSmokeCommand)
    payload = run_remote_provider_smoke_suite(
        resolver=service.repository.remote_tool_resolver,
        providers=list(request.providers),
        include_write=request.include_write,
        allow_write=request.allow_write,
        require_configured=request.require_configured,
        params={
            "service_id": request.service_id,
            "incident_id": request.incident_id,
            "limit": request.limit,
            "search_query": request.search_query,
            "runbook_query": request.runbook_query,
            "draft_id": request.draft_id,
            "channel_type": request.channel_type,
            "title": request.title,
            "body_markdown": request.body_markdown,
            "fact_set_version": request.fact_set_version,
        },
    )
    response = RemoteProviderSmokeResponse.model_validate(payload)
    record = service.repository.record_remote_provider_smoke_run(
        request_payload=request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
        actor_context=service._build_audit_context(auth_context, request_id=request_id),
    )
    return RemoteProviderSmokeResponse.model_validate(
        response.model_dump(mode="json")
        | {
            "diagnostic_run_id": record.diagnostic_run_id,
            "created_at": record.created_at,
        }
    )


def list_remote_provider_smoke_runs(
    service,
    *,
    limit: int = 10,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    provider: str | None = None,
) -> list[RemoteProviderSmokeRunRecord]:
    if limit < 1:
        raise ValueError("INVALID_REMOTE_PROVIDER_SMOKE_HISTORY_LIMIT")
    normalized_actor_user_id = str(actor_user_id or "").strip() or None
    normalized_request_id = str(request_id or "").strip() or None
    normalized_provider = str(provider or "").strip() or None
    return service.repository.list_remote_provider_smoke_runs(
        limit=limit,
        actor_user_id=normalized_actor_user_id,
        request_id=normalized_request_id,
        provider=normalized_provider,
    )


def summarize_remote_provider_smoke_runs(
    service,
    *,
    limit: int = 50,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    provider: str | None = None,
) -> RemoteProviderSmokeHistorySummary:
    runs = list_remote_provider_smoke_runs(
        service,
        limit=limit,
        actor_user_id=actor_user_id,
        request_id=request_id,
        provider=provider,
    )
    normalized_provider = str(provider or "").strip() or None
    provider_map: dict[str, dict[str, Any]] = {}
    for run in runs:
        for result in run.response.results:
            if normalized_provider is not None and result.provider != normalized_provider:
                continue
            summary = provider_map.setdefault(
                result.provider,
                {
                    "provider": result.provider,
                    "run_count": 0,
                    "success_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "consecutive_failure_count": 0,
                    "consecutive_non_success_count": 0,
                    "last_status": None,
                    "last_reason": None,
                    "last_seen_at": None,
                    "last_success_at": None,
                    "last_failure_at": None,
                    "last_skipped_at": None,
                    "last_diagnostic_run_id": None,
                    "latest_effective_mode": None,
                    "latest_backend_id": None,
                    "latest_strict_remote_required": False,
                    "_failure_streak_active": True,
                    "_non_success_streak_active": True,
                },
            )
            summary["run_count"] += 1
            summary[f"{result.status}_count"] += 1
            if summary["last_seen_at"] is None:
                summary["last_status"] = result.status
                summary["last_reason"] = result.reason
                summary["last_seen_at"] = run.created_at
                summary["last_diagnostic_run_id"] = run.diagnostic_run_id
                summary["latest_effective_mode"] = result.capability.effective_mode
                summary["latest_backend_id"] = result.capability.backend_id
                summary["latest_strict_remote_required"] = bool(
                    result.capability.details.get("strict_remote_required")
                )
            if summary["_failure_streak_active"]:
                if result.status == "failed":
                    summary["consecutive_failure_count"] += 1
                else:
                    summary["_failure_streak_active"] = False
            if summary["_non_success_streak_active"]:
                if result.status != "success":
                    summary["consecutive_non_success_count"] += 1
                else:
                    summary["_non_success_streak_active"] = False
            if result.status == "success" and summary["last_success_at"] is None:
                summary["last_success_at"] = run.created_at
            if result.status == "failed" and summary["last_failure_at"] is None:
                summary["last_failure_at"] = run.created_at
            if result.status == "skipped" and summary["last_skipped_at"] is None:
                summary["last_skipped_at"] = run.created_at
    providers = [
        RemoteProviderSmokeProviderSummary.model_validate(payload)
        for payload in sorted(
            [
                {
                    key: value
                    for key, value in payload.items()
                    if not str(key).startswith("_")
                }
                for payload in provider_map.values()
            ],
            key=lambda item: (
                item["last_seen_at"] is None,
                item["last_seen_at"],
                item["provider"],
            ),
            reverse=True,
        )
    ]
    return RemoteProviderSmokeHistorySummary(
        scanned_run_count=len(runs),
        provider_count=len(providers),
        providers=providers,
    )


def get_health_status(service) -> HealthResponse:
    capabilities = get_runtime_capabilities(service)
    return HealthResponse(
        status="ok",
        product="opsgraph",
        runtime_summary=HealthRuntimeSummary(
            model_provider_mode=capabilities.model_provider.effective_mode,
            model_backend_id=capabilities.model_provider.backend_id,
            tooling_modes={
                capability_name: capability.effective_mode
                for capability_name, capability in capabilities.tooling.items()
            },
            tooling_backends={
                capability_name: capability.backend_id
                for capability_name, capability in capabilities.tooling.items()
            },
            auth_mode=(capabilities.auth.mode if capabilities.auth is not None else None),
            auth_source=(capabilities.auth.source if capabilities.auth is not None else None),
            auth_header_fallback_enabled=(
                capabilities.auth.header_fallback_enabled
                if capabilities.auth is not None
                else False
            ),
            auth_demo_seed_enabled=(
                capabilities.auth.demo_seed_enabled
                if capabilities.auth is not None
                else False
            ),
            auth_bootstrap_admin_configured=(
                capabilities.auth.bootstrap_admin_configured
                if capabilities.auth is not None
                else False
            ),
            runtime_provider_alert_level=(
                capabilities.runtime_provider_alert.level
                if capabilities.runtime_provider_alert is not None
                else None
            ),
            runtime_provider_alert_count=(
                capabilities.runtime_provider_alert.active_alert_count
                if capabilities.runtime_provider_alert is not None
                else 0
            ),
            remote_provider_smoke_alert_level=(
                capabilities.remote_provider_smoke_alert.level
                if capabilities.remote_provider_smoke_alert is not None
                else None
            ),
            remote_provider_smoke_alert_count=(
                capabilities.remote_provider_smoke_alert.active_alert_count
                if capabilities.remote_provider_smoke_alert is not None
                else 0
            ),
            replay_worker_status=(
                capabilities.replay_worker.status
                if capabilities.replay_worker is not None
                else None
            ),
            replay_worker_last_seen_at=(
                capabilities.replay_worker.last_seen_at
                if capabilities.replay_worker is not None
                else None
            ),
            replay_worker_workspace_id=(
                capabilities.replay_worker.workspace_id
                if capabilities.replay_worker is not None
                else None
            ),
            replay_worker_remaining_queued_count=(
                capabilities.replay_worker.remaining_queued_count
                if capabilities.replay_worker is not None
                else None
            ),
            replay_worker_alert_level=(
                capabilities.replay_worker_alert.level
                if capabilities.replay_worker_alert is not None
                and (
                    capabilities.replay_worker is not None
                    or bool(capabilities.replay_worker_history)
                )
                else None
            ),
        ),
    )
