from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from .auth import OpsGraphBootstrapAdminSeed, SharedPlatformBackedOpsGraphAuthService
from .product_gateway import OpsGraphProductModelGateway
from .replay_fixtures import replay_fixture_root
from .repository import SqlAlchemyOpsGraphRepository
from .shared_runtime import load_shared_agent_platform
from .service import OpsGraphAppService
from .tool_adapters import register_opsgraph_product_tool_adapters
from .worker import OpsGraphReplayWorker, OpsGraphReplayWorkerSupervisor

SUPPORTED_WORKFLOW_NAMES = (
    "opsgraph_incident_response",
    "opsgraph_retrospective",
)


def _resolve_int_setting(*, explicit: int | None, env_var: str, default: int) -> int:
    if explicit is not None:
        return explicit
    raw = os.getenv(env_var)
    if raw is None or raw == "":
        return default
    return int(raw)


def _parse_bool_setting(raw: str, *, env_var: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{env_var} must be one of true/false, 1/0, yes/no, or on/off.")


def _resolve_bool_setting(*, explicit: bool | None, env_var: str, default: bool) -> bool:
    if explicit is not None:
        return explicit
    raw = os.getenv(env_var)
    if raw is None or raw == "":
        return default
    return _parse_bool_setting(raw, env_var=env_var)


def _resolve_database_url(database_url: str | None = None) -> str:
    return database_url or "sqlite+pysqlite:///:memory:"


def _is_ephemeral_database_url(database_url: str | None = None) -> bool:
    resolved_database_url = _resolve_database_url(database_url)
    return resolved_database_url.startswith("sqlite") and (
        ":memory:" in resolved_database_url or "mode=memory" in resolved_database_url
    )


def _resolve_bootstrap_admin_seed() -> OpsGraphBootstrapAdminSeed | None:
    email = (os.getenv("OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = os.getenv("OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD") or ""
    if bool(email) != bool(password):
        raise ValueError(
            "OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL and OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD must be set together."
        )
    if not email:
        return None
    display_name = (os.getenv("OPSGRAPH_BOOTSTRAP_ADMIN_DISPLAY_NAME") or "").strip() or "OpsGraph Admin"
    organization_slug = (os.getenv("OPSGRAPH_BOOTSTRAP_ORG_SLUG") or "").strip() or "opsgraph"
    organization_name = (os.getenv("OPSGRAPH_BOOTSTRAP_ORG_NAME") or "").strip() or "OpsGraph"
    return OpsGraphBootstrapAdminSeed(
        email=email,
        password=password,
        display_name=display_name,
        organization_slug=organization_slug,
        organization_name=organization_name,
    )


def _resolve_auth_defaults(database_url: str | None = None) -> tuple[bool, bool, OpsGraphBootstrapAdminSeed | None]:
    is_ephemeral_database = _is_ephemeral_database_url(database_url)
    allow_header_fallback = _resolve_bool_setting(
        explicit=None,
        env_var="OPSGRAPH_ALLOW_HEADER_AUTH_FALLBACK",
        default=is_ephemeral_database,
    )
    seed_demo_users = _resolve_bool_setting(
        explicit=None,
        env_var="OPSGRAPH_SEED_DEMO_AUTH",
        default=is_ephemeral_database,
    )
    return allow_header_fallback, seed_demo_users, _resolve_bootstrap_admin_seed()


def _resolve_replay_worker_alert_thresholds(
    *,
    warning_consecutive_failures: int | None = None,
    critical_consecutive_failures: int | None = None,
) -> tuple[int, int]:
    return (
        _resolve_int_setting(
            explicit=warning_consecutive_failures,
            env_var="OPSGRAPH_REPLAY_ALERT_WARNING_CONSECUTIVE_FAILURES",
            default=1,
        ),
        _resolve_int_setting(
            explicit=critical_consecutive_failures,
            env_var="OPSGRAPH_REPLAY_ALERT_CRITICAL_CONSECUTIVE_FAILURES",
            default=3,
        ),
    )


def _resolve_remote_provider_smoke_alert_thresholds(
    *,
    warning_consecutive_failures: int | None = None,
    critical_consecutive_failures: int | None = None,
) -> tuple[int, int]:
    return (
        _resolve_int_setting(
            explicit=warning_consecutive_failures,
            env_var="OPSGRAPH_REMOTE_SMOKE_ALERT_WARNING_CONSECUTIVE_FAILURES",
            default=1,
        ),
        _resolve_int_setting(
            explicit=critical_consecutive_failures,
            env_var="OPSGRAPH_REMOTE_SMOKE_ALERT_CRITICAL_CONSECUTIVE_FAILURES",
            default=3,
        ),
    )


def _build_registry():
    ap = load_shared_agent_platform()
    base_registry = ap.build_workflow_registry()
    registry = ap.WorkflowRegistry()
    for workflow_name in SUPPORTED_WORKFLOW_NAMES:
        registry.register(base_registry.get(workflow_name))
    return ap, registry


def list_supported_workflows() -> tuple[str, ...]:
    return SUPPORTED_WORKFLOW_NAMES


def _create_runtime_engine(database_url: str | None = None):
    resolved_database_url = _resolve_database_url(database_url)
    if resolved_database_url.startswith("sqlite"):
        engine_kwargs: dict[str, object] = {
            "connect_args": {"check_same_thread": False},
        }
        if ":memory:" in resolved_database_url or "mode=memory" in resolved_database_url:
            engine_kwargs["poolclass"] = StaticPool
        return create_engine(resolved_database_url, **engine_kwargs)
    return create_engine(resolved_database_url)


def build_runtime_components(
    *,
    database_url: str | None = None,
    replay_worker_alert_warning_consecutive_failures: int | None = None,
    replay_worker_alert_critical_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_warning_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_critical_consecutive_failures: int | None = None,
) -> dict[str, Any]:
    ap, registry = _build_registry()
    catalog = ap.build_default_runtime_catalog()
    prompt_service = ap.PromptAssemblyService(catalog)
    tool_executor = ap.ToolExecutor(catalog)
    runtime_engine = _create_runtime_engine(database_url)
    runtime_stores = None
    try:
        runtime_stores = ap.create_sqlalchemy_runtime_stores(engine=runtime_engine)
        repository = SqlAlchemyOpsGraphRepository.from_runtime_stores(runtime_stores)
        allow_header_fallback, seed_demo_users, bootstrap_admin = _resolve_auth_defaults(database_url)
        auth_service = SharedPlatformBackedOpsGraphAuthService.from_runtime_stores(
            runtime_stores,
            allow_header_fallback=allow_header_fallback,
            seed_demo_users=seed_demo_users,
            bootstrap_admin=bootstrap_admin,
        )
        register_opsgraph_product_tool_adapters(tool_executor, repository)
        model_gateway = OpsGraphProductModelGateway()
        execution_service = ap.WorkflowExecutionService(
            prompt_service,
            model_gateway=model_gateway,
            tool_executor=tool_executor,
            state_store=runtime_stores.state_store,
            checkpoint_store=runtime_stores.checkpoint_store,
            replay_store=runtime_stores.replay_store,
            outbox_store=runtime_stores.outbox_store,
        )
        components = {
            "catalog": catalog,
            "prompt_service": prompt_service,
            "workflow_registry": registry,
            "model_gateway": model_gateway,
            "tool_executor": tool_executor,
            "runtime_stores": runtime_stores,
            "execution_service": execution_service,
            "shared_platform": ap,
            "repository": repository,
            "auth_service": auth_service,
            "replay_fixture_store": ap.FileReplayFixtureStore(replay_fixture_root()),
        }
        components["api_service"] = ap.WorkflowApiService(
            registry,
            execution_service,
            runtime_stores=runtime_stores,
        )
        (
            warning_consecutive_failures,
            critical_consecutive_failures,
        ) = _resolve_replay_worker_alert_thresholds(
            warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
            critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        )
        components["replay_worker_alert_thresholds"] = {
            "warning_consecutive_failures": warning_consecutive_failures,
            "critical_consecutive_failures": critical_consecutive_failures,
        }
        (
            smoke_warning_consecutive_failures,
            smoke_critical_consecutive_failures,
        ) = _resolve_remote_provider_smoke_alert_thresholds(
            warning_consecutive_failures=remote_provider_smoke_alert_warning_consecutive_failures,
            critical_consecutive_failures=remote_provider_smoke_alert_critical_consecutive_failures,
        )
        components["remote_provider_smoke_alert_thresholds"] = {
            "warning_consecutive_failures": smoke_warning_consecutive_failures,
            "critical_consecutive_failures": smoke_critical_consecutive_failures,
        }
        return components
    except Exception:
        if runtime_stores is not None:
            runtime_stores.dispose()
        else:
            runtime_engine.dispose()
        raise


def build_execution_service(
    *,
    database_url: str | None = None,
    replay_worker_alert_warning_consecutive_failures: int | None = None,
    replay_worker_alert_critical_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_warning_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_critical_consecutive_failures: int | None = None,
):
    components = build_runtime_components(
        database_url=database_url,
        replay_worker_alert_warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
        replay_worker_alert_critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        remote_provider_smoke_alert_warning_consecutive_failures=remote_provider_smoke_alert_warning_consecutive_failures,
        remote_provider_smoke_alert_critical_consecutive_failures=remote_provider_smoke_alert_critical_consecutive_failures,
    )
    return components["execution_service"]


def build_api_service(
    *,
    database_url: str | None = None,
    replay_worker_alert_warning_consecutive_failures: int | None = None,
    replay_worker_alert_critical_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_warning_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_critical_consecutive_failures: int | None = None,
):
    components = build_runtime_components(
        database_url=database_url,
        replay_worker_alert_warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
        replay_worker_alert_critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        remote_provider_smoke_alert_warning_consecutive_failures=remote_provider_smoke_alert_warning_consecutive_failures,
        remote_provider_smoke_alert_critical_consecutive_failures=remote_provider_smoke_alert_critical_consecutive_failures,
    )
    return components["api_service"]


def build_app_service(
    *,
    database_url: str | None = None,
    replay_worker_alert_warning_consecutive_failures: int | None = None,
    replay_worker_alert_critical_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_warning_consecutive_failures: int | None = None,
    remote_provider_smoke_alert_critical_consecutive_failures: int | None = None,
) -> OpsGraphAppService:
    components = build_runtime_components(
        database_url=database_url,
        replay_worker_alert_warning_consecutive_failures=replay_worker_alert_warning_consecutive_failures,
        replay_worker_alert_critical_consecutive_failures=replay_worker_alert_critical_consecutive_failures,
        remote_provider_smoke_alert_warning_consecutive_failures=remote_provider_smoke_alert_warning_consecutive_failures,
        remote_provider_smoke_alert_critical_consecutive_failures=remote_provider_smoke_alert_critical_consecutive_failures,
    )
    return OpsGraphAppService(
        components["api_service"],
        repository=components["repository"],
        runtime_stores=components["runtime_stores"],
        auth_service=components["auth_service"],
        shared_platform=components["shared_platform"],
        workflow_registry=components["workflow_registry"],
        prompt_service=components["prompt_service"],
        replay_fixture_store=components["replay_fixture_store"],
        replay_worker_alert_warning_consecutive_failures=components["replay_worker_alert_thresholds"][
            "warning_consecutive_failures"
        ],
        replay_worker_alert_critical_consecutive_failures=components["replay_worker_alert_thresholds"][
            "critical_consecutive_failures"
        ],
        remote_provider_smoke_alert_warning_consecutive_failures=components["remote_provider_smoke_alert_thresholds"][
            "warning_consecutive_failures"
        ],
        remote_provider_smoke_alert_critical_consecutive_failures=components["remote_provider_smoke_alert_thresholds"][
            "critical_consecutive_failures"
        ],
    )


def build_fastapi_app(*, database_url: str | None = None):
    from .routes import create_fastapi_app

    ap = load_shared_agent_platform()
    return ap.build_managed_fastapi_app(
        service_factory=lambda: build_app_service(database_url=database_url),
        app_factory=create_fastapi_app,
    )


def build_replay_worker(
    *,
    database_url: str | None = None,
    workspace_id: str = "ops-ws-1",
    limit: int = 20,
) -> OpsGraphReplayWorker:
    return OpsGraphReplayWorker(
        build_app_service(database_url=database_url),
        workspace_id=workspace_id,
        limit=limit,
    )


def build_replay_worker_supervisor(
    *,
    database_url: str | None = None,
    workspace_id: str = "ops-ws-1",
    limit: int = 20,
) -> OpsGraphReplayWorkerSupervisor:
    return build_replay_worker(
        database_url=database_url,
        workspace_id=workspace_id,
        limit=limit,
    ).build_supervisor()
