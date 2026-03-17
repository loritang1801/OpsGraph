from __future__ import annotations

from typing import Any

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


def build_runtime_components(*, database_url: str | None = None) -> dict[str, Any]:
    ap, registry = _build_registry()
    components = ap.build_demo_runtime_components(database_url=database_url)
    components["shared_platform"] = ap
    components["repository"] = SqlAlchemyOpsGraphRepository.from_runtime_stores(components["runtime_stores"])
    components["workflow_registry"] = registry
    components["replay_fixture_store"] = ap.FileReplayFixtureStore(replay_fixture_root())
    components["api_service"] = ap.WorkflowApiService(
        registry,
        components["execution_service"],
        runtime_stores=components["runtime_stores"],
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
