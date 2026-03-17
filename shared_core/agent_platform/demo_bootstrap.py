from __future__ import annotations

from typing import Any

from .api_service import WorkflowApiService
from .build_samples import (
    register_auditflow_demo_gateway_responses,
    register_auditflow_demo_tool_adapters,
    register_opsgraph_demo_gateway_responses,
    register_opsgraph_demo_tool_adapters,
)
from .fastapi_adapter import create_fastapi_app
from .model_gateway import StaticModelGateway
from .runtime import PromptAssemblyService
from .service import WorkflowExecutionService
from .sqlalchemy_stores import SqlAlchemyRuntimeStores, create_sqlalchemy_runtime_stores
from .tool_executor import ToolExecutor
from .workflow_definitions import build_workflow_registry


def build_demo_runtime_components(
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    from .bootstrap import build_default_runtime_catalog

    catalog = build_default_runtime_catalog()
    prompt_service = PromptAssemblyService(catalog)
    workflow_registry = build_workflow_registry()
    model_gateway = StaticModelGateway()
    tool_executor = ToolExecutor(catalog)
    runtime_stores: SqlAlchemyRuntimeStores = create_sqlalchemy_runtime_stores(database_url)

    register_auditflow_demo_gateway_responses(model_gateway)
    register_opsgraph_demo_gateway_responses(model_gateway)
    register_auditflow_demo_tool_adapters(tool_executor)
    register_opsgraph_demo_tool_adapters(tool_executor)

    execution_service = WorkflowExecutionService(
        prompt_service,
        model_gateway=model_gateway,
        tool_executor=tool_executor,
        state_store=runtime_stores.state_store,
        checkpoint_store=runtime_stores.checkpoint_store,
        replay_store=runtime_stores.replay_store,
        outbox_store=runtime_stores.outbox_store,
    )
    api_service = WorkflowApiService(
        workflow_registry,
        execution_service,
        runtime_stores=runtime_stores,
    )

    return {
        "catalog": catalog,
        "prompt_service": prompt_service,
        "workflow_registry": workflow_registry,
        "model_gateway": model_gateway,
        "tool_executor": tool_executor,
        "runtime_stores": runtime_stores,
        "execution_service": execution_service,
        "api_service": api_service,
    }


def build_demo_api_service(*, database_url: str | None = None) -> WorkflowApiService:
    components = build_demo_runtime_components(database_url=database_url)
    return components["api_service"]


def build_demo_fastapi_app(*, database_url: str | None = None):
    api_service = build_demo_api_service(database_url=database_url)
    return create_fastapi_app(api_service)
