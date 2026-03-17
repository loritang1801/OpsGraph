from __future__ import annotations

from typing import Any, TypeVar

from .api_models import (
    DispatchOutboxResponse,
    ReplayWorkflowRequest,
    ResumeWorkflowRequest,
    StartWorkflowRequest,
    WorkflowDefinitionSummary,
    WorkflowExecutionResponse,
)
from .service import WorkflowExecutionService
from .workflow_registry import WorkflowRegistry

RequestT = TypeVar(
    "RequestT",
    StartWorkflowRequest,
    ResumeWorkflowRequest,
    ReplayWorkflowRequest,
)


class WorkflowApiService:
    def __init__(
        self,
        workflow_registry: WorkflowRegistry,
        execution_service: WorkflowExecutionService,
        *,
        runtime_stores=None,
    ) -> None:
        self.workflow_registry = workflow_registry
        self.execution_service = execution_service
        self.runtime_stores = runtime_stores

    def list_workflows(self) -> list[WorkflowDefinitionSummary]:
        return [
            WorkflowDefinitionSummary(
                workflow_name=definition.workflow_name,
                workflow_type=definition.workflow_type,
                description=definition.description,
            )
            for definition in self.workflow_registry.list()
        ]

    @staticmethod
    def _coerce_request(request: RequestT | dict[str, Any], model_type: type[RequestT]) -> RequestT:
        if isinstance(request, model_type):
            return request
        if isinstance(request, dict):
            return model_type.model_validate(request)
        raise TypeError(f"Expected {model_type.__name__} or dict, got {type(request).__name__}")

    def start_workflow(
        self,
        request: StartWorkflowRequest | dict[str, Any],
    ) -> WorkflowExecutionResponse:
        request = self._coerce_request(request, StartWorkflowRequest)
        definition = self.workflow_registry.get(request.workflow_name)
        initial_state = definition.initial_state_builder(
            request.workflow_run_id,
            request.input_payload,
            request.state_overrides,
        )
        result = self.execution_service.run_workflow(
            workflow_run_id=request.workflow_run_id,
            workflow_type=definition.workflow_type,
            initial_state=initial_state,
            steps=definition.steps,
            source_builders=definition.source_builders,
        )
        return WorkflowExecutionResponse(
            workflow_name=definition.workflow_name,
            workflow_run_id=result.workflow_run_id,
            workflow_type=result.workflow_type,
            current_state=str(result.final_state.get("current_state", "")),
            checkpoint_seq=int(result.final_state.get("checkpoint_seq", 0)),
            emitted_events=[event for step in result.step_results for event in step.emitted_events],
        )

    def resume_workflow(
        self,
        request: ResumeWorkflowRequest | dict[str, Any],
    ) -> WorkflowExecutionResponse:
        request = self._coerce_request(request, ResumeWorkflowRequest)
        if request.workflow_name is None:
            raise ValueError("resume_workflow currently requires workflow_name")
        definition = self.workflow_registry.get(request.workflow_name)
        result = self.execution_service.resume_workflow(
            workflow_run_id=request.workflow_run_id,
            steps=definition.steps,
            source_builders=definition.source_builders,
        )
        return WorkflowExecutionResponse(
            workflow_name=definition.workflow_name,
            workflow_run_id=result.workflow_run_id,
            workflow_type=result.workflow_type,
            current_state=str(result.final_state.get("current_state", "")),
            checkpoint_seq=int(result.final_state.get("checkpoint_seq", 0)),
            emitted_events=[event for step in result.step_results for event in step.emitted_events],
        )

    def replay_workflow(
        self,
        request: ReplayWorkflowRequest | dict[str, Any],
    ) -> WorkflowExecutionResponse:
        request = self._coerce_request(request, ReplayWorkflowRequest)
        definition = self.workflow_registry.get(request.workflow_name)
        initial_state = definition.initial_state_builder(
            request.workflow_run_id,
            request.input_payload,
            request.state_overrides,
        )
        result = self.execution_service.replay_workflow(
            workflow_run_id=request.workflow_run_id,
            workflow_type=definition.workflow_type,
            initial_state=initial_state,
            steps=definition.steps,
            source_builders=definition.source_builders,
        )
        return WorkflowExecutionResponse(
            workflow_name=definition.workflow_name,
            workflow_run_id=result.workflow_run_id,
            workflow_type=result.workflow_type,
            current_state=str(result.final_state.get("current_state", "")),
            checkpoint_seq=int(result.final_state.get("checkpoint_seq", 0)),
            emitted_events=[event for step in result.step_results for event in step.emitted_events],
        )

    def dispatch_outbox(self, handler) -> DispatchOutboxResponse:
        result = self.execution_service.dispatch_outbox(handler)
        return DispatchOutboxResponse(
            attempted_count=result.attempted_count,
            dispatched_count=result.dispatched_count,
            failed_event_ids=result.failed_event_ids,
        )

    def close(self) -> None:
        if self.runtime_stores is not None and hasattr(self.runtime_stores, "dispose"):
            self.runtime_stores.dispose()
