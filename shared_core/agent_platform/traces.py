from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .shared import SchemaModel, SharedAgentOutputEnvelope, ToolResultEnvelope


class PromptAssemblyTrace(SchemaModel):
    bundle_id: str
    bundle_version: str
    agent_name: str
    model_profile_id: str
    response_schema_ref: str
    variable_names: list[str] = Field(default_factory=list)
    tool_manifest_names: list[str] = Field(default_factory=list)
    assembled_at: datetime


class ToolExecutionTrace(SchemaModel):
    tool_call_id: str
    tool_name: str
    tool_version: str
    adapter_type: str
    node_name: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    session_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    status: str
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class AgentInvocationTrace(SchemaModel):
    agent_name: str
    bundle_id: str
    bundle_version: str
    model_profile_id: str
    response_schema_ref: str
    status: str
    citation_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class NodeExecutionTrace(SchemaModel):
    node_name: str
    node_kind: str
    workflow_run_id: str
    workflow_type: str
    state_before: str
    state_after: str
    emitted_events: list[str] = Field(default_factory=list)
    prompt_trace: PromptAssemblyTrace
    agent_trace: AgentInvocationTrace
    tool_traces: list[ToolExecutionTrace] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class AgentInvocationResult(SchemaModel):
    agent_output: SharedAgentOutputEnvelope
    tool_traces: list[ToolExecutionTrace] = Field(default_factory=list)
    tool_results: list[ToolResultEnvelope] = Field(default_factory=list)
