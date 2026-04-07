from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from .connectors import EnvConfiguredOpsGraphRemoteToolResolver
from .repository import (
    ApprovalTaskRow,
    HypothesisRow,
    IncidentFactRow,
    IncidentRow,
    SignalRow,
    SqlAlchemyOpsGraphRepository,
    TimelineEventRow,
)
from .shared_runtime import load_shared_agent_platform


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _extract_deployment_ids(refs: list[dict] | None) -> list[str]:
    deployment_ids: list[str] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        if str(ref.get("kind") or "") == "deployment" and ref.get("id"):
            deployment_ids.append(str(ref["id"]))
        locator = ref.get("locator")
        if isinstance(locator, dict) and locator.get("deployment_id"):
            deployment_ids.append(str(locator["deployment_id"]))
    deduped: list[str] = []
    seen: set[str] = set()
    for deployment_id in deployment_ids:
        if deployment_id in seen:
            continue
        seen.add(deployment_id)
        deduped.append(deployment_id)
    return deduped


def _service_profile(service_id: str) -> dict[str, object]:
    normalized = service_id.strip() or "service"
    if "checkout" in normalized:
        return {
            "name": normalized,
            "owner_team": "payments-sre",
            "dependency_names": ["postgres", "redis", "edge-gateway"],
            "runbook_refs": [
                f"runbook-{normalized}-rollback",
                f"runbook-{normalized}-db-saturation",
            ],
        }
    if "payments" in normalized:
        return {
            "name": normalized,
            "owner_team": "payments-sre",
            "dependency_names": ["postgres", "kafka", "vault"],
            "runbook_refs": [
                f"runbook-{normalized}-rollback",
                f"runbook-{normalized}-latency",
            ],
        }
    if "catalog" in normalized:
        return {
            "name": normalized,
            "owner_team": "commerce-platform",
            "dependency_names": ["postgres", "elasticsearch", "redis"],
            "runbook_refs": [
                f"runbook-{normalized}-cache-flush",
                f"runbook-{normalized}-rollback",
            ],
        }
    prefix = normalized.split("-", 1)[0]
    return {
        "name": normalized,
        "owner_team": f"{prefix}-team",
        "dependency_names": ["postgres"],
        "runbook_refs": [f"runbook-{normalized}-stability"],
    }


class OpsGraphDatabaseAdapter:
    def __init__(self, repository: SqlAlchemyOpsGraphRepository) -> None:
        self.repository = repository

    def execute(self, *, tool, call, arguments):
        if tool.tool_name == "signal.read":
            return self._read_signals(tool=tool, call=call, arguments=arguments)
        if tool.tool_name == "incident.read_timeline":
            return self._read_timeline(tool=tool, call=call, arguments=arguments)
        raise ValueError(f"Unsupported opsgraph database tool: {tool.tool_name}")

    def _read_signals(self, *, tool, call, arguments):
        with self.repository.session_factory() as session:
            stmt = select(SignalRow).where(SignalRow.signal_id.in_(list(arguments.signal_ids)))
            if str(call.subject_type) == "incident":
                stmt = stmt.where(SignalRow.incident_id == str(call.subject_id))
            rows = session.scalars(stmt.order_by(SignalRow.fired_at.asc())).all()
            return {
                "status": "success",
                "normalized_payload": {
                    "signals": [
                        {
                            "signal_id": row.signal_id,
                            "source": row.source,
                            "correlation_key": row.dedupe_key,
                            "summary": row.title,
                            "observed_at": _normalize_timestamp(row.fired_at),
                        }
                        for row in rows
                    ]
                },
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": f"opsgraph://signals/{','.join(arguments.signal_ids)}",
                },
                "warnings": [],
            }

    def _read_timeline(self, *, tool, call, arguments):
        incident_id = str(arguments.incident_id)
        if str(call.subject_type) == "incident" and str(call.subject_id) != incident_id:
            raise KeyError(incident_id)
        with self.repository.session_factory() as session:
            rows = session.scalars(
                select(TimelineEventRow)
                .where(TimelineEventRow.incident_id == incident_id)
                .order_by(TimelineEventRow.created_at.desc())
                .limit(max(1, min(int(arguments.limit), 200)))
            ).all()
            timeline = []
            for row in rows:
                visibility = "external" if row.kind in {"comms_published"} else "internal"
                if arguments.visibility != "all" and visibility != arguments.visibility:
                    continue
                timeline.append(
                    {
                        "timeline_event_id": row.event_id,
                        "event_type": row.kind,
                        "created_at": _normalize_timestamp(row.created_at),
                        "summary": row.summary,
                        "visibility": visibility,
                    }
                )
            return {
                "status": "success",
                "normalized_payload": {"timeline": timeline},
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": f"opsgraph://incidents/{incident_id}/timeline",
                },
                "warnings": [],
            }


class ContextBundleReaderAdapter:
    def __init__(
        self,
        repository: SqlAlchemyOpsGraphRepository,
        *,
        remote_provider: EnvConfiguredOpsGraphRemoteToolResolver | None = None,
    ) -> None:
        self.repository = repository
        self._remote_provider = remote_provider or EnvConfiguredOpsGraphRemoteToolResolver()

    def execute(self, *, tool, call, arguments):
        del call
        payload = self.repository.read_context_bundle(
            str(arguments.incident_id),
            context_bundle_id=(
                str(arguments.context_bundle_id)
                if arguments.context_bundle_id not in {None, ""}
                else None
            ),
        )
        connection_id = None
        with self.repository.session_factory() as session:
            incident_row = session.get(IncidentRow, str(arguments.incident_id))
        if incident_row is not None:
            remote_result = self._remote_provider.fetch_change_context(
                service_id=incident_row.service_name,
                incident_id=incident_row.incident_id,
                limit=3,
            )
            if remote_result is not None:
                connection_id = remote_result.connection_id
                changes = [
                    dict(item)
                    for item in remote_result.normalized_payload.get("changes", [])
                    if isinstance(item, dict)
                ]
                if changes:
                    payload["refs"] = list(payload.get("refs", [])) + [
                        {"kind": "change_ticket", "id": str(item.get("ticket_ref") or item.get("change_id"))}
                        for item in changes
                        if item.get("ticket_ref") or item.get("change_id")
                    ]
                    payload["summary"] = (
                        f"{payload['summary']} Recent changes: "
                        + "; ".join(
                            str(item.get("summary") or item.get("ticket_ref") or item.get("change_id"))
                            for item in changes[:2]
                        )
                    ).strip()
                else:
                    payload["missing_sources"] = list(payload.get("missing_sources", [])) + ["change_tracking"]
        return {
            "status": "success",
            "normalized_payload": payload,
            "provenance": {
                "adapter_type": tool.adapter_type,
                "fetched_at": _utcnow_iso(),
                "source_locator": f"opsgraph://incidents/{arguments.incident_id}/context",
                "connection_id": connection_id,
            },
            "warnings": [],
        }


class GitHubDeploymentAdapter:
    def __init__(
        self,
        repository: SqlAlchemyOpsGraphRepository,
        *,
        remote_provider: EnvConfiguredOpsGraphRemoteToolResolver | None = None,
    ) -> None:
        self.repository = repository
        self._remote_provider = remote_provider or EnvConfiguredOpsGraphRemoteToolResolver()

    def describe_capability(self) -> dict[str, object]:
        return self._remote_provider.describe_capability(
            "deployment_lookup",
            local_backend_id="heuristic-github-adapter",
            remote_backend_id="http-deployment-provider",
        )

    def execute(self, *, tool, call, arguments):
        del call
        service_id = str(arguments.service_id)
        remote_result = self._remote_provider.fetch_deployments(
            service_id=service_id,
            incident_id=(str(arguments.incident_id) if arguments.incident_id is not None else None),
            limit=int(arguments.limit),
        )
        if remote_result is not None:
            return {
                "status": "success",
                "normalized_payload": dict(remote_result.normalized_payload),
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "connection_id": remote_result.connection_id,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": remote_result.source_locator,
                },
                "warnings": list(remote_result.warnings),
            }
        with self.repository.session_factory() as session:
            incident_row = (
                session.get(IncidentRow, arguments.incident_id)
                if arguments.incident_id is not None
                else session.scalars(
                    select(IncidentRow)
                    .where(IncidentRow.service_name == service_id)
                    .order_by(IncidentRow.updated_at.desc())
                    .limit(1)
                ).first()
            )
            reference_rows: list[IncidentFactRow | HypothesisRow] = []
            if incident_row is not None:
                reference_rows.extend(
                    session.scalars(
                        select(IncidentFactRow)
                        .where(IncidentFactRow.incident_id == incident_row.incident_id)
                        .where(IncidentFactRow.status == "confirmed")
                    ).all()
                )
                reference_rows.extend(
                    session.scalars(
                        select(HypothesisRow)
                        .where(HypothesisRow.incident_id == incident_row.incident_id)
                        .where(HypothesisRow.status != "rejected")
                    ).all()
                )
            deployment_ids: list[str] = []
            for row in reference_rows:
                refs = row.source_refs if isinstance(row, IncidentFactRow) else row.evidence_refs
                deployment_ids.extend(_extract_deployment_ids(refs))
            if not deployment_ids:
                deployment_ids = ["deploy-123" if "checkout" in service_id else f"deploy-{service_id}-latest"]
            deployed_at_origin = incident_row.opened_at if incident_row is not None else datetime.now(UTC)
            deployments = []
            for index, deployment_id in enumerate(deployment_ids[: max(1, min(int(arguments.limit), 10))]):
                deployments.append(
                    {
                        "deployment_id": deployment_id,
                        "commit_ref": deployment_id.replace("deploy-", "")[:12] or "unknown",
                        "actor": "release-bot",
                        "deployed_at": _normalize_timestamp(
                            deployed_at_origin - timedelta(minutes=5 * (index + 1))
                        ),
                    }
                )
            return {
                "status": "success",
                "normalized_payload": {"deployments": deployments},
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "connection_id": "local-github",
                    "fetched_at": _utcnow_iso(),
                    "source_locator": f"github://services/{service_id}/deployments",
                },
                "warnings": [],
            }


class ServiceRegistryAdapter:
    def __init__(
        self,
        repository: SqlAlchemyOpsGraphRepository,
        *,
        remote_provider: EnvConfiguredOpsGraphRemoteToolResolver | None = None,
    ) -> None:
        self.repository = repository
        self._remote_provider = remote_provider or EnvConfiguredOpsGraphRemoteToolResolver()

    def describe_capability(self) -> dict[str, object]:
        return self._remote_provider.describe_capability(
            "service_registry",
            local_backend_id="heuristic-service-registry",
            remote_backend_id="http-service-registry-provider",
        )

    def execute(self, *, tool, call, arguments):
        del call
        remote_result = self._remote_provider.fetch_services(
            service_id=(str(arguments.service_id) if arguments.service_id is not None else None),
            search_query=(str(arguments.search_query) if arguments.search_query is not None else None),
            limit=5,
        )
        if remote_result is not None:
            return {
                "status": "success",
                "normalized_payload": dict(remote_result.normalized_payload),
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "connection_id": remote_result.connection_id,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": remote_result.source_locator,
                },
                "warnings": list(remote_result.warnings),
            }
        with self.repository.session_factory() as session:
            if arguments.service_id is not None:
                service_ids = [str(arguments.service_id)]
            else:
                rows = session.scalars(
                    select(IncidentRow.service_name).order_by(IncidentRow.service_name.asc())
                ).all()
                service_ids = [str(value) for value in rows if value]
                if arguments.search_query is not None:
                    query = str(arguments.search_query).strip().lower()
                    service_ids = [value for value in service_ids if query in value.lower()]
            deduped_ids: list[str] = []
            seen: set[str] = set()
            for service_id in service_ids:
                if service_id in seen:
                    continue
                seen.add(service_id)
                deduped_ids.append(service_id)
            services = []
            for service_id in deduped_ids[:5]:
                profile = _service_profile(service_id)
                services.append(
                    {
                        "service_id": service_id,
                        "name": str(profile["name"]),
                        "owner_team": str(profile["owner_team"]),
                        "dependency_names": list(profile["dependency_names"]),
                        "runbook_refs": list(profile["runbook_refs"]),
                    }
                )
            return {
                "status": "success",
                "normalized_payload": {"services": services},
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": "opsgraph://service-registry",
                },
                "warnings": [],
            }


class RunbookSearchAdapter:
    def __init__(
        self,
        repository: SqlAlchemyOpsGraphRepository,
        *,
        remote_provider: EnvConfiguredOpsGraphRemoteToolResolver | None = None,
    ) -> None:
        self.repository = repository
        self._remote_provider = remote_provider or EnvConfiguredOpsGraphRemoteToolResolver()

    def describe_capability(self) -> dict[str, object]:
        return self._remote_provider.describe_capability(
            "runbook_search",
            local_backend_id="heuristic-runbook-index",
            remote_backend_id="http-runbook-provider",
        )

    def execute(self, *, tool, call, arguments):
        del call
        service_id = str(arguments.service_id)
        remote_result = self._remote_provider.fetch_runbooks(
            service_id=service_id,
            query=str(arguments.query),
            limit=int(arguments.limit),
        )
        if remote_result is not None:
            return {
                "status": "success",
                "normalized_payload": dict(remote_result.normalized_payload),
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "connection_id": remote_result.connection_id,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": remote_result.source_locator,
                },
                "warnings": list(remote_result.warnings),
            }
        normalized_query = str(arguments.query).strip().lower()
        runbooks = [
            {
                "runbook_id": f"runbook-{service_id}-rollback",
                "title": f"Rollback {service_id} safely",
                "excerpt": f"Rollback the latest {service_id} deployment and verify service health.",
                "score": 0.93 if "rollback" in normalized_query or "deploy" in normalized_query else 0.84,
            },
            {
                "runbook_id": f"runbook-{service_id}-stability",
                "title": f"Stabilize elevated error rates on {service_id}",
                "excerpt": f"Use confirmed facts to isolate {service_id} error-rate regressions.",
                "score": 0.89 if "error" in normalized_query or "latency" in normalized_query else 0.8,
            },
            {
                "runbook_id": f"runbook-{service_id}-dependencies",
                "title": f"Check downstream dependencies for {service_id}",
                "excerpt": f"Inspect database and cache dependencies before escalating {service_id}.",
                "score": 0.78,
            },
        ]
        runbooks = sorted(runbooks, key=lambda item: item["score"], reverse=True)[: max(1, min(int(arguments.limit), 10))]
        return {
            "status": "success",
            "normalized_payload": {"runbooks": runbooks},
            "provenance": {
                "adapter_type": tool.adapter_type,
                "fetched_at": _utcnow_iso(),
                "source_locator": f"opsgraph://runbooks/{service_id}?query={arguments.query}",
            },
            "warnings": [],
        }


class ChannelPolicyAdapter:
    def execute(self, *, tool, call, arguments):
        del call
        max_length = {
            "internal_slack": 2000,
            "exec_email": 4000,
            "statuspage": 800,
            "customer_email": 1600,
        }.get(str(arguments.channel_type), 1200)
        preview_body = str(arguments.draft_body).strip()
        policy_warnings: list[str] = []
        if len(preview_body) > max_length:
            preview_body = preview_body[:max_length]
            policy_warnings.append("draft_truncated_to_channel_limit")
        if str(arguments.channel_type) in {"statuspage", "customer_email"}:
            policy_warnings.append("external_channel_requires_approval")
        return {
            "status": "success",
            "normalized_payload": {
                "preview_body": preview_body,
                "max_length": max_length,
                "policy_warnings": policy_warnings,
            },
            "provenance": {
                "adapter_type": tool.adapter_type,
                "fetched_at": _utcnow_iso(),
                "source_locator": f"opsgraph://channel-policy/{arguments.channel_type}",
            },
            "warnings": [],
        }


class ApprovalStoreAdapter:
    def __init__(self, repository: SqlAlchemyOpsGraphRepository) -> None:
        self.repository = repository

    def execute(self, *, tool, call, arguments):
        del call
        with self.repository.session_factory() as session:
            rows = session.scalars(
                select(ApprovalTaskRow)
                .where(ApprovalTaskRow.approval_task_id.in_(list(arguments.approval_task_ids)))
                .order_by(ApprovalTaskRow.created_at.asc())
            ).all()
            return {
                "status": "success",
                "normalized_payload": {
                    "approvals": [
                        {
                            "approval_task_id": row.approval_task_id,
                            "status": row.status,
                            "resolved_at": (
                                _normalize_timestamp(row.updated_at) if row.status != "pending" else None
                            ),
                        }
                        for row in rows
                    ]
                },
                "provenance": {
                    "adapter_type": tool.adapter_type,
                    "fetched_at": _utcnow_iso(),
                    "source_locator": "opsgraph://approval-tasks",
                },
                "warnings": [],
            }


def register_opsgraph_product_tool_adapters(tool_executor, repository: SqlAlchemyOpsGraphRepository) -> None:
    remote_provider = EnvConfiguredOpsGraphRemoteToolResolver()
    repository.remote_tool_resolver = remote_provider
    tool_executor.register_adapter("opsgraph_database", OpsGraphDatabaseAdapter(repository))
    tool_executor.register_adapter(
        "context_bundle_reader",
        ContextBundleReaderAdapter(repository, remote_provider=remote_provider),
    )
    tool_executor.register_adapter("github", GitHubDeploymentAdapter(repository, remote_provider=remote_provider))
    tool_executor.register_adapter("service_registry", ServiceRegistryAdapter(repository, remote_provider=remote_provider))
    tool_executor.register_adapter("vector_store", RunbookSearchAdapter(repository, remote_provider=remote_provider))
    tool_executor.register_adapter("channel_policy", ChannelPolicyAdapter())
    tool_executor.register_adapter("approval_store", ApprovalStoreAdapter(repository))


def describe_opsgraph_product_tool_capabilities(tool_executor=None) -> dict[str, dict[str, object]]:
    shared_platform = load_shared_agent_platform()
    registered_adapters: dict[str, Any] = getattr(tool_executor, "_adapters", {}) if tool_executor is not None else {}
    resolver = EnvConfiguredOpsGraphRemoteToolResolver()

    specs = {
        "incident_store": {
            "adapter_type": "opsgraph_database",
            "backend_id": "sqlalchemy-repository",
            "details": {
                "tool_names": ["signal.read", "incident.read_timeline"],
                "scope": "incident_workspace",
            },
        },
        "context_bundle": {
            "adapter_type": "context_bundle_reader",
            "backend_id": "repository-context",
            "details": {
                "tool_names": ["context_bundle.read"],
                "scope": "incident_workspace",
            },
        },
        "deployment_lookup": {
            "adapter_type": "github",
            "backend_id": "heuristic-github-adapter",
            "details": {
                "tool_names": ["deployment.lookup"],
                "scope": "service_release_context",
            },
        },
        "service_registry": {
            "adapter_type": "service_registry",
            "backend_id": "heuristic-service-registry",
            "details": {
                "tool_names": ["service_registry.lookup"],
                "scope": "service_metadata",
            },
        },
        "runbook_search": {
            "adapter_type": "vector_store",
            "backend_id": "heuristic-runbook-index",
            "details": {
                "tool_names": ["runbook.search"],
                "scope": "runbook_catalog",
            },
        },
        "channel_policy": {
            "adapter_type": "channel_policy",
            "backend_id": "channel-policy-local",
            "details": {
                "tool_names": ["comms.channel_preview"],
                "scope": "channel_constraints",
            },
        },
        "change_context": {
            "adapter_type": "context_bundle_reader",
            "backend_id": "repository-context-only",
            "details": {
                "tool_names": ["context_bundle.read"],
                "scope": "change_tracking",
            },
        },
        "comms_publish": {
            "adapter_type": "channel_policy",
            "backend_id": "local-publish-fallback",
            "details": {
                "tool_names": ["incident.publish_comms"],
                "scope": "external_channel_delivery",
            },
        },
        "approval_store": {
            "adapter_type": "approval_store",
            "backend_id": "sqlalchemy-approval-store",
            "details": {
                "tool_names": ["approval_task.read_state"],
                "scope": "approval_tasks",
            },
        },
    }

    capabilities: dict[str, dict[str, object]] = {}
    for key, spec in specs.items():
        adapter_type = str(spec["adapter_type"])
        adapter = registered_adapters.get(adapter_type)
        if tool_executor is not None and adapter is None:
            descriptor = shared_platform.RuntimeCapabilityDescriptor(
                requested_mode="local",
                effective_mode="unavailable",
                backend_id="missing-adapter",
                fallback_reason="ADAPTER_NOT_REGISTERED",
                details={},
            ).as_dict()
        elif adapter is not None and hasattr(adapter, "describe_capability"):
            descriptor = dict(adapter.describe_capability())
        elif key == "deployment_lookup":
            descriptor = resolver.describe_capability(
                "deployment_lookup",
                local_backend_id="heuristic-github-adapter",
                remote_backend_id="http-deployment-provider",
            )
        elif key == "runbook_search":
            descriptor = resolver.describe_capability(
                "runbook_search",
                local_backend_id="heuristic-runbook-index",
                remote_backend_id="http-runbook-provider",
            )
        elif key == "service_registry":
            descriptor = resolver.describe_capability(
                "service_registry",
                local_backend_id="heuristic-service-registry",
                remote_backend_id="http-service-registry-provider",
            )
        elif key == "change_context":
            descriptor = resolver.describe_capability(
                "change_context",
                local_backend_id="repository-context-only",
                remote_backend_id="http-change-context-provider",
            )
        elif key == "comms_publish":
            descriptor = resolver.describe_capability(
                "comms_publish",
                local_backend_id="local-publish-fallback",
                remote_backend_id="http-comms-publish-provider",
            )
        else:
            descriptor = shared_platform.RuntimeCapabilityDescriptor(
                requested_mode="local",
                effective_mode="local",
                backend_id=str(spec["backend_id"]),
                fallback_reason=None,
                details={},
            ).as_dict()
        descriptor["details"] = {
            "adapter_type": adapter_type,
            **dict(spec["details"]),
            **dict(descriptor.get("details") or {}),
        }
        capabilities[key] = descriptor
    return capabilities
