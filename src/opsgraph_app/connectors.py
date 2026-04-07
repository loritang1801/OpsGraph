from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

from .shared_runtime import load_shared_agent_platform


class HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float): ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        follow_redirects: bool,
        timeout: float,
    ): ...


@dataclass(slots=True)
class RemoteToolFetchResult:
    normalized_payload: dict[str, object]
    source_locator: str
    connection_id: str | None = None
    warnings: list[str] = field(default_factory=list)


class EnvConfiguredOpsGraphRemoteToolResolver:
    def __init__(self, *, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client
        self._shared_platform = load_shared_agent_platform()
        self._last_remote_error: dict[str, str | None] = {}
        self._last_runtime_fallback_reason: dict[str, str | None] = {}

    def _env_value(self, name: str) -> str | None:
        return self._shared_platform.env_value(name)

    @staticmethod
    def _provider_prefix(provider: str) -> str:
        return f"OPSGRAPH_{provider.strip().upper()}"

    def _fetch_mode(self, provider: str) -> str:
        prefix = self._provider_prefix(provider)
        configured_mode = self._env_value(f"{prefix}_PROVIDER") or self._env_value(f"{prefix}_FETCH_MODE")
        return self._shared_platform.normalize_requested_mode(
            configured_mode,
            allowed_modes=("auto", "local", "http"),
            default="auto",
        )

    def _env_bool(self, name: str) -> bool | None:
        raw = self._env_value(name)
        if raw is None:
            return None
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"INVALID_{name}")

    def _provider_policy(self, provider: str) -> dict[str, object]:
        prefix = self._provider_prefix(provider)
        requested_mode = self._fetch_mode(provider)
        has_url_template = self._env_value(f"{prefix}_URL_TEMPLATE") is not None
        decision = self._shared_platform.resolve_remote_mode(
            requested_mode=requested_mode,
            allowed_modes=("auto", "local", "http"),
            local_mode="local",
            remote_mode="http",
            has_remote_configuration=has_url_template,
            strict_remote_mode="http",
            strict_missing_error=f"{prefix}_URL_TEMPLATE",
            auto_fallback_reason=f"{prefix}_HTTP_TEMPLATE_NOT_CONFIGURED",
        )
        configured_allow_fallback = self._env_bool(f"{prefix}_ALLOW_FALLBACK")
        fallback_policy_source = "default"
        if configured_allow_fallback is not None:
            allow_fallback = configured_allow_fallback
            fallback_policy_source = "env"
        else:
            allow_fallback = decision.allow_fallback
        if decision.requested_mode == "local":
            allow_fallback = False
        return {
            "prefix": prefix,
            "has_url_template": has_url_template,
            "decision": self._shared_platform.RuntimeModeDecision(
                requested_mode=decision.requested_mode,
                effective_mode=decision.effective_mode,
                use_remote=decision.use_remote,
                allow_fallback=allow_fallback,
                fallback_reason=decision.fallback_reason,
            ),
            "allow_fallback": allow_fallback,
            "fallback_policy_source": fallback_policy_source,
            "strict_remote_required": decision.requested_mode != "local" and not allow_fallback,
        }

    @staticmethod
    def _runtime_request_failure_code(prefix: str) -> str:
        return f"{prefix}_REMOTE_REQUEST_FAILED"

    def describe_capability(
        self,
        provider: str,
        *,
        local_backend_id: str,
        remote_backend_id: str,
    ) -> dict[str, object]:
        policy = self._provider_policy(provider)
        prefix = str(policy["prefix"])
        decision = policy["decision"]
        has_url_template = bool(policy["has_url_template"])
        has_auth = (
            self._env_value(f"{prefix}_AUTH_TOKEN") is not None
            or (
                self._env_value(f"{prefix}_USERNAME") is not None
                and self._env_value(f"{prefix}_PASSWORD") is not None
            )
        )
        status_lookup_configured = (
            provider == "comms_publish"
            and self._env_value(f"{prefix}_STATUS_URL_TEMPLATE") is not None
        )
        connection_id = self._env_value(f"{prefix}_CONNECTION_ID")
        configured_backend_id = self._env_value(f"{prefix}_BACKEND_ID")
        effective_mode = str(decision.effective_mode)
        fallback_reason = decision.fallback_reason
        if bool(policy["strict_remote_required"]) and not decision.use_remote:
            effective_mode = "unavailable"
        runtime_fallback_reason = self._last_runtime_fallback_reason.get(provider)
        if runtime_fallback_reason is not None:
            effective_mode = "local"
            fallback_reason = runtime_fallback_reason
        if effective_mode == "http":
            backend_id = configured_backend_id or remote_backend_id
        elif effective_mode == "local":
            backend_id = local_backend_id
        else:
            backend_id = configured_backend_id or remote_backend_id
        return self._shared_platform.RuntimeCapabilityDescriptor(
            requested_mode=decision.requested_mode,
            effective_mode=effective_mode,
            backend_id=backend_id,
            fallback_reason=fallback_reason,
            details={
                "has_url_template": has_url_template,
                "configured": has_url_template,
                "has_auth": has_auth,
                "connection_id": connection_id,
                "fallback_enabled": bool(policy["allow_fallback"]),
                "fallback_policy_source": str(policy["fallback_policy_source"]),
                "strict_remote_required": bool(policy["strict_remote_required"]),
                "last_remote_error": self._last_remote_error.get(provider),
                "healthy": self._last_remote_error.get(provider) is None,
                "write_enabled": provider == "comms_publish" and effective_mode == "http",
                "delivery_confirmable": bool(status_lookup_configured),
                "delivery_status_url_configured": bool(status_lookup_configured),
            },
        ).as_dict()

    def _record_remote_success(self, provider: str) -> None:
        self._last_remote_error[provider] = None
        self._last_runtime_fallback_reason[provider] = None

    def _handle_remote_exception(self, provider: str, *, policy: dict[str, object], exc: Exception) -> None:
        self._last_remote_error[provider] = type(exc).__name__
        self._last_runtime_fallback_reason[provider] = None
        decision = policy["decision"]
        if decision.requested_mode == "http" or not bool(policy["allow_fallback"]):
            raise exc
        self._last_runtime_fallback_reason[provider] = self._runtime_request_failure_code(str(policy["prefix"]))

    def _enforce_provider_availability(self, provider: str, *, policy: dict[str, object]) -> None:
        decision = policy["decision"]
        if decision.use_remote or not bool(policy["strict_remote_required"]):
            return
        raise ValueError(str(decision.fallback_reason or f"{policy['prefix']}_HTTP_TEMPLATE_NOT_CONFIGURED"))

    def _timeout_seconds(self, provider: str) -> float:
        configured = self._env_value(f"{self._provider_prefix(provider)}_TIMEOUT_SECONDS")
        if configured is None:
            return 20.0
        try:
            parsed = float(configured)
        except ValueError:
            parsed = 20.0
        return max(1.0, parsed)

    def _build_http_client(self) -> HttpClient:
        if self._http_client is not None:
            return self._http_client
        import httpx

        self._http_client = httpx.Client()
        return self._http_client

    def _build_headers(self, provider: str) -> dict[str, str]:
        prefix = self._provider_prefix(provider)
        headers: dict[str, str] = {}
        headers_json = self._env_value(f"{prefix}_HEADERS_JSON")
        if headers_json is not None:
            parsed = json.loads(headers_json)
            if isinstance(parsed, dict):
                headers.update({str(key): str(value) for key, value in parsed.items()})
        auth_type = (self._env_value(f"{prefix}_AUTH_TYPE") or "").lower()
        bearer_token = self._env_value(f"{prefix}_AUTH_TOKEN")
        username = self._env_value(f"{prefix}_USERNAME")
        password = self._env_value(f"{prefix}_PASSWORD")
        if auth_type in {"", "bearer"} and bearer_token is not None:
            headers.setdefault("Authorization", f"Bearer {bearer_token}")
        elif auth_type == "basic" and username is not None and password is not None:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers.setdefault("Authorization", f"Basic {encoded}")
        headers.setdefault("Accept", "application/json, text/plain;q=0.9, */*;q=0.8")
        return headers

    def _resolve_target_url(
        self,
        provider: str,
        *,
        explicit_template: str | None = None,
        **params: object,
    ) -> str | None:
        prefix = self._provider_prefix(provider)
        template = explicit_template or self._env_value(f"{prefix}_URL_TEMPLATE")
        if template is None:
            return None
        values: dict[str, str] = {}
        for key, value in params.items():
            raw_value = "" if value is None else str(value)
            values[key] = quote(raw_value, safe="")
            values[f"{key}_raw"] = raw_value
        return template.format(**values)

    @staticmethod
    def _lookup_nested(value: Any, path: tuple[str, ...]) -> Any:
        current = value
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @classmethod
    def _flatten_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            parts = [cls._flatten_text(item) for item in value]
            return "\n".join(part for part in parts if part)
        if isinstance(value, dict):
            ordered_parts = []
            for key in ("login", "name", "title", "summary", "description", "body", "content", "text", "value"):
                flattened = cls._flatten_text(value.get(key))
                if flattened:
                    ordered_parts.append(flattened)
            if ordered_parts:
                return "\n".join(ordered_parts)
            nested_parts = [cls._flatten_text(item) for item in value.values()]
            return "\n".join(part for part in nested_parts if part)
        return ""

    @classmethod
    def _first_text(cls, value: Any, *paths: tuple[str, ...]) -> str | None:
        for path in paths:
            candidate = cls._flatten_text(cls._lookup_nested(value, path))
            if candidate:
                return candidate
        return None

    @staticmethod
    def _coerce_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, str):
            return value.strip() or None
        return None

    @staticmethod
    def _extract_items(payload: Any, *, collection_keys: tuple[str, ...]) -> list[dict[str, object]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in collection_keys:
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [payload]
        return []

    @staticmethod
    def _safe_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            items: list[str] = []
            for item in value:
                flattened = cls._flatten_text(item)
                if flattened:
                    items.append(flattened)
            return items
        flattened = cls._flatten_text(value)
        return [flattened] if flattened else []

    @classmethod
    def _reference_list(cls, value: Any, *, id_key: str) -> list[str]:
        if isinstance(value, list):
            items: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    identifier = cls._first_text(item, (id_key,), ("id",), ("slug",), ("name",))
                    if identifier:
                        items.append(identifier)
                else:
                    flattened = cls._flatten_text(item)
                    if flattened:
                        items.append(flattened)
            return items
        flattened = cls._flatten_text(value)
        return [flattened] if flattened else []

    @staticmethod
    def _response_payload(response: Any) -> Any:
        if hasattr(response, "json"):
            try:
                return response.json()
            except Exception:
                pass
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            return {}
        return json.loads(text)

    @staticmethod
    def _coerce_error_payload(value: Any) -> dict[str, object] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return {
                str(key): item
                for key, item in value.items()
                if isinstance(key, str)
            }
        flattened = EnvConfiguredOpsGraphRemoteToolResolver._flatten_text(value)
        if flattened:
            return {"message": flattened}
        return None

    @classmethod
    def _coerce_bool(cls, value: Any, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        flattened = cls._flatten_text(value).strip().lower()
        if flattened in {"1", "true", "yes", "on"}:
            return True
        if flattened in {"0", "false", "no", "off"}:
            return False
        return default

    def _lookup_comms_delivery(
        self,
        *,
        incident_id: str,
        draft_id: str,
        channel_type: str,
        fact_set_version: int,
        published_message_ref: str | None,
    ) -> RemoteToolFetchResult | None:
        provider = "comms_publish"
        prefix = self._provider_prefix(provider)
        template = self._env_value(f"{prefix}_STATUS_URL_TEMPLATE")
        if template is None:
            return None
        url = self._resolve_target_url(
            provider,
            explicit_template=template,
            incident_id=incident_id,
            draft_id=draft_id,
            channel_type=channel_type,
            fact_set_version=fact_set_version,
            published_message_ref=published_message_ref,
        )
        if url is None:
            return None
        try:
            response = self._build_http_client().get(
                url,
                headers=self._build_headers(provider),
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            raw_delivery_state = self._first_text(
                payload,
                ("delivery_state",),
                ("status",),
                ("result", "status"),
            )
            delivery_state, warnings = self._normalize_comms_delivery_state(raw_delivery_state)
            normalized_payload: dict[str, object] = {
                "published_message_ref": self._first_text(
                    payload,
                    ("published_message_ref",),
                    ("message_id",),
                    ("id",),
                    ("result", "message_id"),
                )
                or published_message_ref,
                "delivery_state": delivery_state,
                "delivery_confirmed": delivery_state in {"published", "failed"},
                "provider_delivery_status": raw_delivery_state,
            }
            delivery_error = self._coerce_error_payload(
                self._lookup_nested(payload, ("delivery_error",))
                or self._lookup_nested(payload, ("error",))
                or self._lookup_nested(payload, ("result", "error"))
            )
            if delivery_error is not None:
                normalized_payload["delivery_error"] = delivery_error
            return RemoteToolFetchResult(
                normalized_payload=normalized_payload,
                source_locator=str(getattr(response, "url", "") or url),
                connection_id=self._env_value(f"{prefix}_CONNECTION_ID"),
                warnings=warnings,
            )
        except Exception as exc:
            self._last_remote_error[provider] = type(exc).__name__
            return RemoteToolFetchResult(
                normalized_payload={},
                source_locator=str(template),
                connection_id=self._env_value(f"{prefix}_CONNECTION_ID"),
                warnings=[f"delivery_status_lookup_failed:{type(exc).__name__}"],
            )

    @classmethod
    def _normalize_comms_delivery_state(cls, value: Any) -> tuple[str, list[str]]:
        normalized = cls._flatten_text(value).strip().lower()
        if normalized in {"", "published", "delivered", "sent", "success", "ok"}:
            return "published", []
        if normalized in {"accepted", "queued", "pending", "scheduled", "dispatched", "processing"}:
            warnings = [] if normalized == "accepted" else [f"normalized delivery_state '{normalized}' -> 'accepted'"]
            return "accepted", warnings
        if normalized in {"failed", "error", "rejected"}:
            warnings = [] if normalized == "failed" else [f"normalized delivery_state '{normalized}' -> 'failed'"]
            return "failed", warnings
        return "published", [f"unrecognized delivery_state '{normalized}' -> 'published'"]

    def fetch_deployments(
        self,
        *,
        service_id: str,
        incident_id: str | None,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "deployment_lookup"
        policy = self._provider_policy(provider)
        decision = policy["decision"]
        if decision.requested_mode == "local":
            return None
        self._enforce_provider_availability(provider, policy=policy)
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            incident_id=incident_id,
            limit=limit,
        )
        if url is None:
            if decision.requested_mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
            if not bool(policy["allow_fallback"]):
                raise ValueError(str(decision.fallback_reason or f"{self._provider_prefix(provider)}_HTTP_TEMPLATE_NOT_CONFIGURED"))
            return None
        try:
            response = self._build_http_client().get(
                url,
                headers=self._build_headers(provider),
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            deployments = []
            for index, item in enumerate(
                self._extract_items(payload, collection_keys=("deployments", "items", "results", "data"))
            ):
                deployment_id = self._first_text(
                    item,
                    ("deployment_id",),
                    ("id",),
                    ("deploy_id",),
                    ("release_id",),
                    ("metadata", "deployment_id"),
                )
                if deployment_id is None:
                    continue
                commit_ref = self._first_text(
                    item,
                    ("commit_ref",),
                    ("commit",),
                    ("commit_sha",),
                    ("sha",),
                    ("revision",),
                    ("metadata", "commit_ref"),
                ) or deployment_id.replace("deploy-", "")[:12]
                actor = self._first_text(
                    item,
                    ("actor",),
                    ("actor", "login"),
                    ("deployed_by",),
                    ("author",),
                    ("creator",),
                    ("creator", "login"),
                    ("user",),
                ) or "remote-provider"
                deployed_at = self._coerce_timestamp(
                    self._lookup_nested(item, ("deployed_at",))
                    or self._lookup_nested(item, ("created_at",))
                    or self._lookup_nested(item, ("timestamp",))
                    or self._lookup_nested(item, ("finished_at",))
                    or self._lookup_nested(item, ("updated_at",))
                    or self._lookup_nested(item, ("time",))
                ) or datetime.now(UTC).isoformat().replace("+00:00", "Z")
                deployments.append(
                    {
                        "deployment_id": deployment_id,
                        "commit_ref": commit_ref,
                        "actor": actor,
                        "deployed_at": deployed_at,
                    }
                )
                if len(deployments) >= max(1, min(int(limit), 10)):
                    break
            source_locator = str(getattr(response, "url", "") or url)
            self._record_remote_success(provider)
            return RemoteToolFetchResult(
                normalized_payload={"deployments": deployments},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception as exc:
            self._handle_remote_exception(provider, policy=policy, exc=exc)
            return None

    def fetch_services(
        self,
        *,
        service_id: str | None,
        search_query: str | None,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "service_registry"
        policy = self._provider_policy(provider)
        decision = policy["decision"]
        if decision.requested_mode == "local":
            return None
        self._enforce_provider_availability(provider, policy=policy)
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            search_query=search_query,
            limit=limit,
        )
        if url is None:
            if decision.requested_mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
            if not bool(policy["allow_fallback"]):
                raise ValueError(str(decision.fallback_reason or f"{self._provider_prefix(provider)}_HTTP_TEMPLATE_NOT_CONFIGURED"))
            return None
        try:
            response = self._build_http_client().get(
                url,
                headers=self._build_headers(provider),
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            services = []
            for item in self._extract_items(payload, collection_keys=("services", "items", "results", "data")):
                resolved_service_id = self._first_text(
                    item,
                    ("service_id",),
                    ("id",),
                    ("slug",),
                    ("metadata", "service_id"),
                )
                if resolved_service_id is None:
                    continue
                owner_team = self._first_text(
                    item,
                    ("owner_team",),
                    ("team",),
                    ("owner", "team"),
                    ("owner", "name"),
                ) or "unknown-team"
                dependency_names = self._string_list(
                    self._lookup_nested(item, ("dependency_names",))
                    or self._lookup_nested(item, ("dependencies",))
                )
                runbook_refs = self._reference_list(
                    self._lookup_nested(item, ("runbook_refs",))
                    or self._lookup_nested(item, ("runbooks",)),
                    id_key="runbook_id",
                )
                services.append(
                    {
                        "service_id": resolved_service_id,
                        "name": self._first_text(item, ("name",), ("title",), ("display_name",)) or resolved_service_id,
                        "owner_team": owner_team,
                        "dependency_names": dependency_names,
                        "runbook_refs": runbook_refs,
                    }
                )
                if len(services) >= max(1, min(int(limit), 10)):
                    break
            source_locator = str(getattr(response, "url", "") or url)
            self._record_remote_success(provider)
            return RemoteToolFetchResult(
                normalized_payload={"services": services},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception as exc:
            self._handle_remote_exception(provider, policy=policy, exc=exc)
            return None

    def fetch_runbooks(
        self,
        *,
        service_id: str,
        query: str,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "runbook_search"
        policy = self._provider_policy(provider)
        decision = policy["decision"]
        if decision.requested_mode == "local":
            return None
        self._enforce_provider_availability(provider, policy=policy)
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            query=query,
            limit=limit,
        )
        if url is None:
            if decision.requested_mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
            if not bool(policy["allow_fallback"]):
                raise ValueError(str(decision.fallback_reason or f"{self._provider_prefix(provider)}_HTTP_TEMPLATE_NOT_CONFIGURED"))
            return None
        try:
            response = self._build_http_client().get(
                url,
                headers=self._build_headers(provider),
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            runbooks = []
            for index, item in enumerate(
                self._extract_items(payload, collection_keys=("runbooks", "items", "results", "matches", "data"))
            ):
                runbook_id = self._first_text(
                    item,
                    ("runbook_id",),
                    ("id",),
                    ("doc_id",),
                    ("slug",),
                    ("metadata", "runbook_id"),
                )
                if runbook_id is None:
                    continue
                title = self._first_text(
                    item,
                    ("title",),
                    ("name",),
                    ("metadata", "title"),
                ) or runbook_id
                excerpt = self._first_text(
                    item,
                    ("excerpt",),
                    ("summary",),
                    ("description",),
                    ("snippet",),
                    ("body",),
                    ("content",),
                ) or f"Runbook guidance for {service_id}"
                score = self._safe_float(
                    self._lookup_nested(item, ("score",))
                    or self._lookup_nested(item, ("relevance",))
                    or self._lookup_nested(item, ("similarity",))
                    or self._lookup_nested(item, ("rank",)),
                    default=max(0.0, 0.8 - (0.05 * index)),
                )
                runbooks.append(
                    {
                        "runbook_id": runbook_id,
                        "title": title,
                        "excerpt": excerpt,
                        "score": score,
                    }
                )
                if len(runbooks) >= max(1, min(int(limit), 10)):
                    break
            source_locator = str(getattr(response, "url", "") or url)
            self._record_remote_success(provider)
            return RemoteToolFetchResult(
                normalized_payload={"runbooks": runbooks},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception as exc:
            self._handle_remote_exception(provider, policy=policy, exc=exc)
            return None

    def fetch_change_context(
        self,
        *,
        service_id: str,
        incident_id: str | None,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "change_context"
        policy = self._provider_policy(provider)
        decision = policy["decision"]
        if decision.requested_mode == "local":
            return None
        self._enforce_provider_availability(provider, policy=policy)
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            incident_id=incident_id,
            limit=limit,
        )
        if url is None:
            if decision.requested_mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
            if not bool(policy["allow_fallback"]):
                raise ValueError(str(decision.fallback_reason or f"{self._provider_prefix(provider)}_HTTP_TEMPLATE_NOT_CONFIGURED"))
            return None
        try:
            response = self._build_http_client().get(
                url,
                headers=self._build_headers(provider),
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            changes = []
            for item in self._extract_items(payload, collection_keys=("changes", "items", "results", "data")):
                change_id = self._first_text(
                    item,
                    ("change_id",),
                    ("id",),
                    ("ticket_ref",),
                    ("issue_key",),
                    ("key",),
                )
                if change_id is None:
                    continue
                ticket_ref = self._first_text(
                    item,
                    ("ticket_ref",),
                    ("issue_key",),
                    ("jira_key",),
                    ("key",),
                ) or change_id
                summary = self._first_text(
                    item,
                    ("summary",),
                    ("title",),
                    ("description",),
                    ("body",),
                ) or f"Change context for {service_id}"
                status = self._first_text(
                    item,
                    ("status",),
                    ("state",),
                    ("workflow_status",),
                ) or "implemented"
                changed_at = self._coerce_timestamp(
                    self._lookup_nested(item, ("changed_at",))
                    or self._lookup_nested(item, ("updated_at",))
                    or self._lookup_nested(item, ("created_at",))
                    or self._lookup_nested(item, ("timestamp",))
                ) or datetime.now(UTC).isoformat().replace("+00:00", "Z")
                changes.append(
                    {
                        "change_id": change_id,
                        "ticket_ref": ticket_ref,
                        "summary": summary,
                        "status": status,
                        "changed_at": changed_at,
                    }
                )
                if len(changes) >= max(1, min(int(limit), 10)):
                    break
            source_locator = str(getattr(response, "url", "") or url)
            self._record_remote_success(provider)
            return RemoteToolFetchResult(
                normalized_payload={"changes": changes},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception as exc:
            self._handle_remote_exception(provider, policy=policy, exc=exc)
            return None

    def publish_comms(
        self,
        *,
        incident_id: str,
        draft_id: str,
        channel_type: str,
        title: str,
        body_markdown: str,
        fact_set_version: int,
    ) -> RemoteToolFetchResult | None:
        provider = "comms_publish"
        policy = self._provider_policy(provider)
        decision = policy["decision"]
        if decision.requested_mode == "local":
            return None
        self._enforce_provider_availability(provider, policy=policy)
        url = self._resolve_target_url(
            provider,
            incident_id=incident_id,
            draft_id=draft_id,
            channel_type=channel_type,
            fact_set_version=fact_set_version,
        )
        if url is None:
            if decision.requested_mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
            if not bool(policy["allow_fallback"]):
                raise ValueError(str(decision.fallback_reason or f"{self._provider_prefix(provider)}_HTTP_TEMPLATE_NOT_CONFIGURED"))
            return None
        headers = self._build_headers(provider)
        headers.setdefault("Content-Type", "application/json")
        request_payload = {
            "incident_id": incident_id,
            "draft_id": draft_id,
            "channel_type": channel_type,
            "title": title,
            "body_markdown": body_markdown,
            "fact_set_version": fact_set_version,
        }
        try:
            response = self._build_http_client().post(
                url,
                headers=headers,
                json=request_payload,
                follow_redirects=True,
                timeout=self._timeout_seconds(provider),
            )
            response.raise_for_status()
            payload = self._response_payload(response)
            published_message_ref = self._first_text(
                payload,
                ("published_message_ref",),
                ("message_id",),
                ("id",),
                ("result", "message_id"),
            )
            raw_delivery_state = self._first_text(
                payload,
                ("delivery_state",),
                ("status",),
                ("result", "status"),
            )
            delivery_state, warnings = self._normalize_comms_delivery_state(raw_delivery_state)
            delivery_error = self._coerce_error_payload(
                self._lookup_nested(payload, ("delivery_error",))
                or self._lookup_nested(payload, ("error",))
                or self._lookup_nested(payload, ("result", "error"))
            )
            delivery_confirmed = self._coerce_bool(
                self._lookup_nested(payload, ("delivery_confirmed",)),
                default=delivery_state in {"published", "failed"},
            )
            provider_delivery_status = raw_delivery_state
            status_lookup_failed = False
            if delivery_state == "accepted":
                lookup_result = self._lookup_comms_delivery(
                    incident_id=incident_id,
                    draft_id=draft_id,
                    channel_type=channel_type,
                    fact_set_version=fact_set_version,
                    published_message_ref=published_message_ref,
                )
                if lookup_result is not None:
                    warnings.extend(list(lookup_result.warnings))
                    status_lookup_failed = any(
                        str(item).startswith("delivery_status_lookup_failed:")
                        for item in lookup_result.warnings
                    )
                    if lookup_result.normalized_payload:
                        delivery_state = str(
                            lookup_result.normalized_payload.get("delivery_state") or delivery_state
                        )
                        delivery_confirmed = bool(
                            lookup_result.normalized_payload.get("delivery_confirmed", delivery_confirmed)
                        )
                        provider_delivery_status = (
                            str(lookup_result.normalized_payload.get("provider_delivery_status"))
                            if lookup_result.normalized_payload.get("provider_delivery_status") is not None
                            else provider_delivery_status
                        )
                        published_message_ref = (
                            str(lookup_result.normalized_payload.get("published_message_ref"))
                            if lookup_result.normalized_payload.get("published_message_ref") not in {None, ""}
                            else published_message_ref
                        )
                        delivery_error = self._coerce_error_payload(
                            lookup_result.normalized_payload.get("delivery_error")
                            if lookup_result.normalized_payload.get("delivery_error") is not None
                            else delivery_error
                        )
            source_locator = str(getattr(response, "url", "") or url)
            if not status_lookup_failed:
                self._record_remote_success(provider)
            normalized_payload: dict[str, object] = {
                "published_message_ref": published_message_ref,
                "delivery_state": delivery_state,
                "delivery_confirmed": delivery_confirmed,
                "provider_delivery_status": provider_delivery_status,
            }
            if delivery_error is not None:
                normalized_payload["delivery_error"] = delivery_error
            return RemoteToolFetchResult(
                normalized_payload=normalized_payload,
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
                warnings=warnings,
            )
        except Exception as exc:
            self._handle_remote_exception(provider, policy=policy, exc=exc)
            return None
