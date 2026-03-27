from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field

from .errors import NodeExecutionError
from .node_runtime import NodeExecutionContext
from .runtime import AssembledPrompt
from .shared import AuthorizationContext, SchemaModel, SharedAgentOutputEnvelope, ToolCallEnvelope
from .tool_executor import ToolExecutor
from .traces import AgentInvocationResult


class ModelGateway(Protocol):
    def generate(self, *, assembled_prompt: AssembledPrompt) -> "ModelGatewayResponse": ...


class PlannedToolCall(SchemaModel):
    tool_name: str
    tool_version: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelGatewayResponse(SchemaModel):
    agent_output: SharedAgentOutputEnvelope
    planned_tool_calls: list[PlannedToolCall] = Field(default_factory=list)


class StaticModelGateway:
    def __init__(self) -> None:
        self._responses: dict[tuple[str, str], ModelGatewayResponse] = {}

    def register_response(
        self,
        *,
        bundle_id: str,
        bundle_version: str,
        response: SharedAgentOutputEnvelope | ModelGatewayResponse,
    ) -> None:
        normalized = (
            response
            if isinstance(response, ModelGatewayResponse)
            else ModelGatewayResponse(agent_output=response)
        )
        self._responses[(bundle_id, bundle_version)] = normalized

    def generate(self, *, assembled_prompt: AssembledPrompt) -> ModelGatewayResponse:
        key = (assembled_prompt.bundle_id, assembled_prompt.bundle_version)
        if key not in self._responses:
            raise NodeExecutionError(
                f"No static model response registered for {assembled_prompt.bundle_id}@{assembled_prompt.bundle_version}"
            )
        return self._responses[key]


class GatewayAgentInvoker:
    def __init__(
        self,
        model_gateway: ModelGateway,
        *,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.tool_executor = tool_executor

    def invoke(
        self,
        *,
        assembled_prompt: AssembledPrompt,
        context: NodeExecutionContext,
    ) -> AgentInvocationResult:
        response = self.model_gateway.generate(assembled_prompt=assembled_prompt)
        if response.planned_tool_calls and self.tool_executor is None:
            raise NodeExecutionError(
                f"Tool calls requested for {assembled_prompt.bundle_id}@{assembled_prompt.bundle_version} "
                "but no ToolExecutor was provided"
            )

        allowed_tools = {
            (tool.tool_name, tool.tool_version)
            for tool in assembled_prompt.tool_manifest
        }
        tool_results = []
        tool_traces = []
        for planned_call in response.planned_tool_calls:
            if (planned_call.tool_name, planned_call.tool_version) not in allowed_tools:
                raise NodeExecutionError(
                    f"Disallowed tool requested by {assembled_prompt.bundle_id}@{assembled_prompt.bundle_version}: "
                    f"{planned_call.tool_name}@{planned_call.tool_version}"
                )
            outcome = self.tool_executor.execute(
                ToolCallEnvelope(
                    tool_call_id=str(uuid4()),
                    tool_name=planned_call.tool_name,
                    tool_version=planned_call.tool_version,
                    workflow_run_id=context.workflow_run_id,
                    node_name=context.node_name,
                    subject_type=context.subject_type or context.workflow_type,
                    subject_id=context.subject_id or context.workflow_run_id,
                    arguments=planned_call.arguments,
                    idempotency_key=(
                        f"{context.workflow_run_id}:{context.node_name}:"
                        f"{planned_call.tool_name}:{context.checkpoint_seq + 1}"
                    ),
                    authorization_context=AuthorizationContext(
                        organization_id=context.organization_id,
                        workspace_id=context.workspace_id,
                        user_id=context.user_id,
                        role=context.role,
                        session_id=context.session_id,
                    ),
                )
            )
            tool_results.append(outcome.envelope)
            tool_traces.append(outcome.trace)

        return AgentInvocationResult(
            agent_output=response.agent_output,
            tool_traces=tool_traces,
            tool_results=tool_results,
        )
