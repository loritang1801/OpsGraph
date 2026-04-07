from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .connectors import EnvConfiguredOpsGraphRemoteToolResolver
from .remote_provider_schemas import build_remote_provider_schema_models


@dataclass(frozen=True, slots=True)
class RemoteProviderSmokeSpec:
    provider: str
    local_backend_id: str
    remote_backend_id: str
    request_schema_filename: str
    response_schema_filename: str
    requires_write: bool = False


_PROVIDER_SPECS: dict[str, RemoteProviderSmokeSpec] = {
    "deployment_lookup": RemoteProviderSmokeSpec(
        provider="deployment_lookup",
        local_backend_id="heuristic-github-adapter",
        remote_backend_id="http-deployment-provider",
        request_schema_filename="deployment_lookup_request.schema.json",
        response_schema_filename="deployment_lookup_response.schema.json",
    ),
    "service_registry": RemoteProviderSmokeSpec(
        provider="service_registry",
        local_backend_id="heuristic-service-registry",
        remote_backend_id="http-service-registry-provider",
        request_schema_filename="service_registry_request.schema.json",
        response_schema_filename="service_registry_response.schema.json",
    ),
    "runbook_search": RemoteProviderSmokeSpec(
        provider="runbook_search",
        local_backend_id="heuristic-runbook-index",
        remote_backend_id="http-runbook-provider",
        request_schema_filename="runbook_search_request.schema.json",
        response_schema_filename="runbook_search_response.schema.json",
    ),
    "change_context": RemoteProviderSmokeSpec(
        provider="change_context",
        local_backend_id="repository-context-only",
        remote_backend_id="http-change-context-provider",
        request_schema_filename="change_context_request.schema.json",
        response_schema_filename="change_context_response.schema.json",
    ),
    "comms_publish": RemoteProviderSmokeSpec(
        provider="comms_publish",
        local_backend_id="local-publish-fallback",
        remote_backend_id="http-comms-publish-provider",
        request_schema_filename="comms_publish_request.schema.json",
        response_schema_filename="comms_publish_response.schema.json",
        requires_write=True,
    ),
}


def available_smoke_providers(*, include_write: bool = False) -> list[str]:
    return [
        provider
        for provider, spec in _PROVIDER_SPECS.items()
        if include_write or not spec.requires_write
    ]


def provider_smoke_spec(provider: str) -> RemoteProviderSmokeSpec:
    try:
        return _PROVIDER_SPECS[provider]
    except KeyError as exc:
        raise ValueError(f"UNKNOWN_REMOTE_PROVIDER:{provider}") from exc


def _request_payload(provider: str, *, params: dict[str, Any]) -> dict[str, Any]:
    if provider == "deployment_lookup":
        return {
            "service_id": str(params["service_id"]),
            "incident_id": str(params["incident_id"]) if params.get("incident_id") is not None else None,
            "limit": int(params["limit"]),
        }
    if provider == "service_registry":
        return {
            "service_id": str(params["service_id"]) if params.get("service_id") is not None else None,
            "search_query": str(params["search_query"]) if params.get("search_query") is not None else None,
            "limit": int(params["limit"]),
        }
    if provider == "runbook_search":
        return {
            "service_id": str(params["service_id"]),
            "query": str(params["runbook_query"]),
            "limit": int(params["limit"]),
        }
    if provider == "change_context":
        return {
            "service_id": str(params["service_id"]),
            "incident_id": str(params["incident_id"]) if params.get("incident_id") is not None else None,
            "limit": int(params["limit"]),
        }
    if provider == "comms_publish":
        return {
            "incident_id": str(params["incident_id"]),
            "draft_id": str(params["draft_id"]),
            "channel_type": str(params["channel_type"]),
            "title": str(params["title"]),
            "body_markdown": str(params["body_markdown"]),
            "fact_set_version": int(params["fact_set_version"]),
        }
    raise ValueError(f"UNSUPPORTED_REMOTE_PROVIDER:{provider}")


def _execute_provider(
    resolver: EnvConfiguredOpsGraphRemoteToolResolver,
    provider: str,
    *,
    request_payload: dict[str, Any],
):
    if provider == "deployment_lookup":
        return resolver.fetch_deployments(
            service_id=str(request_payload["service_id"]),
            incident_id=(
                str(request_payload["incident_id"])
                if request_payload.get("incident_id") is not None
                else None
            ),
            limit=int(request_payload["limit"]),
        )
    if provider == "service_registry":
        return resolver.fetch_services(
            service_id=(
                str(request_payload["service_id"])
                if request_payload.get("service_id") is not None
                else None
            ),
            search_query=(
                str(request_payload["search_query"])
                if request_payload.get("search_query") is not None
                else None
            ),
            limit=int(request_payload["limit"]),
        )
    if provider == "runbook_search":
        return resolver.fetch_runbooks(
            service_id=str(request_payload["service_id"]),
            query=str(request_payload["query"]),
            limit=int(request_payload["limit"]),
        )
    if provider == "change_context":
        return resolver.fetch_change_context(
            service_id=str(request_payload["service_id"]),
            incident_id=(
                str(request_payload["incident_id"])
                if request_payload.get("incident_id") is not None
                else None
            ),
            limit=int(request_payload["limit"]),
        )
    if provider == "comms_publish":
        return resolver.publish_comms(
            incident_id=str(request_payload["incident_id"]),
            draft_id=str(request_payload["draft_id"]),
            channel_type=str(request_payload["channel_type"]),
            title=str(request_payload["title"]),
            body_markdown=str(request_payload["body_markdown"]),
            fact_set_version=int(request_payload["fact_set_version"]),
        )
    raise ValueError(f"UNSUPPORTED_REMOTE_PROVIDER:{provider}")


def run_remote_provider_smoke(
    resolver: EnvConfiguredOpsGraphRemoteToolResolver,
    provider: str,
    *,
    params: dict[str, Any],
    allow_write: bool = False,
) -> dict[str, Any]:
    spec = provider_smoke_spec(provider)
    capability_before = resolver.describe_capability(
        provider,
        local_backend_id=spec.local_backend_id,
        remote_backend_id=spec.remote_backend_id,
    )
    schema_models = build_remote_provider_schema_models()
    request_model = schema_models[spec.request_schema_filename]
    response_model = schema_models[spec.response_schema_filename]
    request_payload = request_model.model_validate(
        _request_payload(provider, params=params)
    ).model_dump(mode="json")
    if spec.requires_write and not allow_write:
        return {
            "provider": provider,
            "status": "skipped",
            "reason": "WRITE_PROVIDER_DISABLED",
            "capability": capability_before,
            "request": request_payload,
        }
    if capability_before["effective_mode"] != "http":
        return {
            "provider": provider,
            "status": "skipped",
            "reason": str(capability_before.get("fallback_reason") or "REMOTE_PROVIDER_NOT_ACTIVE"),
            "capability": capability_before,
            "request": request_payload,
        }
    remote_result = _execute_provider(resolver, provider, request_payload=request_payload)
    capability_after = resolver.describe_capability(
        provider,
        local_backend_id=spec.local_backend_id,
        remote_backend_id=spec.remote_backend_id,
    )
    if remote_result is None:
        return {
            "provider": provider,
            "status": "failed",
            "reason": str(capability_after.get("fallback_reason") or "REMOTE_PROVIDER_RETURNED_NONE"),
            "capability": capability_after,
            "request": request_payload,
        }
    response_payload = response_model.model_validate(
        dict(remote_result.normalized_payload)
    ).model_dump(mode="json")
    return {
        "provider": provider,
        "status": "success",
        "capability": capability_after,
        "request": request_payload,
        "response": response_payload,
        "provenance": {
            "source_locator": remote_result.source_locator,
            "connection_id": remote_result.connection_id,
            "warnings": list(remote_result.warnings),
        },
    }


def run_remote_provider_smoke_suite(
    *,
    resolver: EnvConfiguredOpsGraphRemoteToolResolver | None = None,
    providers: list[str] | None = None,
    include_write: bool = False,
    allow_write: bool = False,
    require_configured: bool = False,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_providers = providers or available_smoke_providers(include_write=include_write)
    active_resolver = resolver or EnvConfiguredOpsGraphRemoteToolResolver()
    shared_params = {
        "service_id": "checkout-api",
        "incident_id": "incident-1",
        "limit": 3,
        "search_query": "checkout api",
        "runbook_query": "rollback elevated 5xx",
        "draft_id": "draft-1",
        "channel_type": "internal_slack",
        "title": "OpsGraph remote provider smoke",
        "body_markdown": "Smoke validation for remote provider delivery.",
        "fact_set_version": 1,
    }
    if params is not None:
        shared_params.update(params)
    results = [
        run_remote_provider_smoke(
            active_resolver,
            provider,
            params=shared_params,
            allow_write=allow_write,
        )
        for provider in selected_providers
    ]
    failed = [item for item in results if item["status"] == "failed"]
    skipped = [item for item in results if item["status"] == "skipped"]
    success = [item for item in results if item["status"] == "success"]
    exit_code = 0
    if failed:
        exit_code = 1
    elif require_configured and skipped:
        exit_code = 1
    return {
        "providers": selected_providers,
        "summary": {
            "success_count": len(success),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
        },
        "results": results,
        "exit_code": exit_code,
    }
