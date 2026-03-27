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

    def describe_capability(
        self,
        provider: str,
        *,
        local_backend_id: str,
        remote_backend_id: str,
    ) -> dict[str, object]:
        requested_mode = self._fetch_mode(provider)
        prefix = self._provider_prefix(provider)
        has_url_template = self._env_value(f"{prefix}_URL_TEMPLATE") is not None
        has_auth = (
            self._env_value(f"{prefix}_AUTH_TOKEN") is not None
            or (
                self._env_value(f"{prefix}_USERNAME") is not None
                and self._env_value(f"{prefix}_PASSWORD") is not None
            )
        )
        connection_id = self._env_value(f"{prefix}_CONNECTION_ID")
        configured_backend_id = self._env_value(f"{prefix}_BACKEND_ID")
        decision = self._shared_platform.resolve_remote_mode(
            requested_mode=requested_mode,
            allowed_modes=("auto", "local", "http"),
            local_mode="local",
            remote_mode="http",
            has_remote_configuration=has_url_template,
            auto_fallback_reason=f"{prefix}_HTTP_TEMPLATE_NOT_CONFIGURED",
        )
        return self._shared_platform.RuntimeCapabilityDescriptor(
            requested_mode=decision.requested_mode,
            effective_mode=decision.effective_mode,
            backend_id=(
                configured_backend_id
                or (remote_backend_id if decision.effective_mode == "http" else local_backend_id)
            ),
            fallback_reason=decision.fallback_reason,
            details={
                "has_url_template": has_url_template,
                "has_auth": has_auth,
                "connection_id": connection_id,
            },
        ).as_dict()

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

    def _resolve_target_url(self, provider: str, **params: object) -> str | None:
        prefix = self._provider_prefix(provider)
        template = self._env_value(f"{prefix}_URL_TEMPLATE")
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

    def fetch_deployments(
        self,
        *,
        service_id: str,
        incident_id: str | None,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "deployment_lookup"
        mode = self._fetch_mode(provider)
        if mode == "local":
            return None
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            incident_id=incident_id,
            limit=limit,
        )
        if url is None:
            if mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
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
            return RemoteToolFetchResult(
                normalized_payload={"deployments": deployments},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception:
            if mode == "http":
                raise
            return None

    def fetch_services(
        self,
        *,
        service_id: str | None,
        search_query: str | None,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "service_registry"
        mode = self._fetch_mode(provider)
        if mode == "local":
            return None
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            search_query=search_query,
            limit=limit,
        )
        if url is None:
            if mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
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
            return RemoteToolFetchResult(
                normalized_payload={"services": services},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception:
            if mode == "http":
                raise
            return None

    def fetch_runbooks(
        self,
        *,
        service_id: str,
        query: str,
        limit: int,
    ) -> RemoteToolFetchResult | None:
        provider = "runbook_search"
        mode = self._fetch_mode(provider)
        if mode == "local":
            return None
        url = self._resolve_target_url(
            provider,
            service_id=service_id,
            query=query,
            limit=limit,
        )
        if url is None:
            if mode == "http":
                raise ValueError(f"{self._provider_prefix(provider)}_URL_TEMPLATE")
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
            return RemoteToolFetchResult(
                normalized_payload={"runbooks": runbooks},
                source_locator=source_locator,
                connection_id=self._env_value(f"{self._provider_prefix(provider)}_CONNECTION_ID"),
            )
        except Exception:
            if mode == "http":
                raise
            return None
