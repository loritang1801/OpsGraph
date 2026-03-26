from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from .auth import SqlAlchemyOpsGraphAuthService
from .replay_fixtures import replay_fixture_root
from .repository import SqlAlchemyOpsGraphRepository
from .shared_runtime import load_shared_agent_platform
from .service import OpsGraphAppService

SUPPORTED_WORKFLOW_NAMES = (
    "opsgraph_incident_response",
    "opsgraph_retrospective",
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
    resolved_database_url = database_url or "sqlite+pysqlite:///:memory:"
    if resolved_database_url.startswith("sqlite"):
        engine_kwargs: dict[str, object] = {
            "connect_args": {"check_same_thread": False},
        }
        if ":memory:" in resolved_database_url or "mode=memory" in resolved_database_url:
            engine_kwargs["poolclass"] = StaticPool
        return create_engine(resolved_database_url, **engine_kwargs)
    return create_engine(resolved_database_url)


def build_runtime_components(*, database_url: str | None = None) -> dict[str, Any]:
    ap, registry = _build_registry()
    runtime_engine = _create_runtime_engine(database_url)
    runtime_stores = ap.create_sqlalchemy_runtime_stores(engine=runtime_engine)
    catalog = ap.build_default_runtime_catalog()
    prompt_service = ap.PromptAssemblyService(catalog)
    model_gateway = ap.StaticModelGateway()
    tool_executor = ap.ToolExecutor(catalog)
    ap.register_auditflow_demo_gateway_responses(model_gateway)
    ap.register_opsgraph_demo_gateway_responses(model_gateway)
    ap.register_auditflow_demo_tool_adapters(tool_executor)
    ap.register_opsgraph_demo_tool_adapters(tool_executor)
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
        "repository": SqlAlchemyOpsGraphRepository.from_runtime_stores(runtime_stores),
        "auth_service": SqlAlchemyOpsGraphAuthService.from_runtime_stores(runtime_stores),
        "replay_fixture_store": ap.FileReplayFixtureStore(replay_fixture_root()),
    }
    components["api_service"] = ap.WorkflowApiService(
        registry,
        execution_service,
        runtime_stores=runtime_stores,
    )
    return components


def build_execution_service(*, database_url: str | None = None):
    components = build_runtime_components(database_url=database_url)
    return components["execution_service"]


def build_api_service(*, database_url: str | None = None):
    components = build_runtime_components(database_url=database_url)
    return components["api_service"]


def build_app_service(*, database_url: str | None = None) -> OpsGraphAppService:
    components = build_runtime_components(database_url=database_url)
    return OpsGraphAppService(
        components["api_service"],
        repository=components["repository"],
        runtime_stores=components["runtime_stores"],
        auth_service=components["auth_service"],
        shared_platform=components["shared_platform"],
        workflow_registry=components["workflow_registry"],
        prompt_service=components["prompt_service"],
        replay_fixture_store=components["replay_fixture_store"],
    )


def build_fastapi_app(*, database_url: str | None = None):
    from .routes import create_fastapi_app

    service = build_app_service(database_url=database_url)
    try:
        return create_fastapi_app(service)
    except Exception:
        service.close()
        raise
