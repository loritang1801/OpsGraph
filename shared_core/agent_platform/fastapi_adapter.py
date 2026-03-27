from __future__ import annotations

from typing import Callable, TypeVar

from .api_service import WorkflowApiService
from .api_models import ReplayWorkflowRequest, ResumeWorkflowRequest, StartWorkflowRequest
from .errors import FastAPIUnavailableError

AppT = TypeVar("AppT")
ServiceT = TypeVar("ServiceT")


def attach_service_lifecycle(app, *, service, state_attr: str) -> None:
    setattr(app.state, state_attr, service)
    if not hasattr(service, "close"):
        return

    def _close_service() -> None:
        service.close()

    shutdown_handlers = getattr(getattr(app, "router", None), "on_shutdown", None)
    if isinstance(shutdown_handlers, list):
        shutdown_handlers.append(_close_service)
        return
    if hasattr(app, "add_event_handler"):
        app.add_event_handler("shutdown", _close_service)
        return
    raise AttributeError("App does not expose a FastAPI-compatible shutdown hook")


def build_managed_fastapi_app(
    *,
    service_factory: Callable[[], ServiceT],
    app_factory: Callable[[ServiceT], AppT],
) -> AppT:
    service = service_factory()
    try:
        return app_factory(service)
    except Exception:
        if hasattr(service, "close"):
            service.close()
        raise


def create_fastapi_app(api_service: WorkflowApiService):
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise FastAPIUnavailableError("fastapi is not installed") from exc

    app = FastAPI(title="SharedAgentCore Workflow API")
    attach_service_lifecycle(app, service=api_service, state_attr="workflow_api_service")

    @app.get("/workflows")
    def list_workflows():
        return api_service.list_workflows()

    @app.post("/workflows/start")
    def start_workflow(request: StartWorkflowRequest):
        return api_service.start_workflow(request)

    @app.post("/workflows/resume")
    def resume_workflow(request: ResumeWorkflowRequest):
        return api_service.resume_workflow(request)

    @app.post("/workflows/replay")
    def replay_workflow(request: ReplayWorkflowRequest):
        return api_service.replay_workflow(request)

    return app
