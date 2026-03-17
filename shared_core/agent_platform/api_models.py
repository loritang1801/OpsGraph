from __future__ import annotations

from pydantic import Field

from .shared import SchemaModel


class StartWorkflowRequest(SchemaModel):
    workflow_name: str
    workflow_run_id: str
    input_payload: dict = Field(default_factory=dict)
    state_overrides: dict = Field(default_factory=dict)


class ResumeWorkflowRequest(SchemaModel):
    workflow_run_id: str
    workflow_name: str | None = None


class ReplayWorkflowRequest(SchemaModel):
    workflow_name: str
    workflow_run_id: str
    input_payload: dict = Field(default_factory=dict)
    state_overrides: dict = Field(default_factory=dict)


class WorkflowExecutionResponse(SchemaModel):
    workflow_name: str
    workflow_run_id: str
    workflow_type: str
    current_state: str
    checkpoint_seq: int
    emitted_events: list[str] = Field(default_factory=list)


class WorkflowDefinitionSummary(SchemaModel):
    workflow_name: str
    workflow_type: str
    description: str


class DispatchOutboxResponse(SchemaModel):
    attempted_count: int
    dispatched_count: int
    failed_event_ids: list[str] = Field(default_factory=list)
