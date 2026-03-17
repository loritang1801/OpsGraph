from __future__ import annotations

from .api_service import WorkflowApiService
from .api_models import ReplayWorkflowRequest, ResumeWorkflowRequest, StartWorkflowRequest
from .errors import FastAPIUnavailableError


def create_fastapi_app(api_service: WorkflowApiService):
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise FastAPIUnavailableError("fastapi is not installed") from exc

    app = FastAPI(title="SharedAgentCore Workflow API")

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
