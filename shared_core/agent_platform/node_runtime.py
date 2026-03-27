from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from typing import Any, Callable, Protocol

from pydantic import Field

from .checkpoints import CheckpointStore, ReplayRecord, ReplayStore, WorkflowCheckpoint
from .errors import NodeExecutionError, OutputValidationError
from .events import EventEmitter, OutboxEvent
from .runtime import AssembledPrompt, PromptAssemblyService, PromptAssemblySources
from .shared import SchemaModel, SharedAgentOutputEnvelope
from .traces import (
    AgentInvocationResult,
    AgentInvocationTrace,
    NodeExecutionTrace,
    PromptAssemblyTrace,
    ToolExecutionTrace,
)


class AgentInvoker(Protocol):
    def invoke(
        self,
        *,
        assembled_prompt: AssembledPrompt,
        context: "NodeExecutionContext",
    ) -> AgentInvocationResult: ...


class StaticAgentInvoker:
    def __init__(self, response: SharedAgentOutputEnvelope) -> None:
        self._response = response

    def invoke(
        self,
        *,
        assembled_prompt: AssembledPrompt,
        context: "NodeExecutionContext",
    ) -> AgentInvocationResult:
        return AgentInvocationResult(agent_output=self._response)


class NodeExecutionContext(SchemaModel):
    node_name: str
    node_kind: str
    workflow_run_id: str
    workflow_type: str
    organization_id: str = "unknown-org"
    workspace_id: str = "unknown-workspace"
    user_id: str | None = None
    role: str | None = None
    session_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    current_state: str
    checkpoint_seq: int = 0
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    bundle_id: str
    bundle_version: str
    prompt_sources: PromptAssemblySources
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeExecutionResult(SchemaModel):
    state_patch: dict[str, Any]
    emitted_events: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: NodeExecutionTrace
    agent_output: SharedAgentOutputEnvelope
    checkpoint: WorkflowCheckpoint | None = None
    replay_record: ReplayRecord | None = None
    emitted_outbox_events: list[OutboxEvent] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)


StatePatchBuilder = Callable[[NodeExecutionContext, SharedAgentOutputEnvelope], dict[str, Any]]


class SpecialistNodeHandler:
    def __init__(
        self,
        *,
        node_name: str,
        node_kind: str,
        success_events: list[str] | None = None,
        state_patch_builder: StatePatchBuilder | None = None,
    ) -> None:
        self.node_name = node_name
        self.node_kind = node_kind
        self.success_events = success_events or []
        self.state_patch_builder = state_patch_builder or (lambda context, output: {})

    def execute(
        self,
        *,
        context: NodeExecutionContext,
        prompt_service: PromptAssemblyService,
        agent_invoker: AgentInvoker,
        event_emitter: EventEmitter | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
    ) -> NodeExecutionResult:
        started_at = datetime.now(UTC)
        try:
            assembled_prompt = prompt_service.assemble(
                bundle_id=context.bundle_id,
                bundle_version=context.bundle_version,
                sources=context.prompt_sources,
            )
            prompt_trace = PromptAssemblyTrace(
                bundle_id=assembled_prompt.bundle_id,
                bundle_version=assembled_prompt.bundle_version,
                agent_name=assembled_prompt.agent_name,
                model_profile_id=assembled_prompt.model_profile_id,
                response_schema_ref=assembled_prompt.response_schema_ref,
                variable_names=sorted(assembled_prompt.resolved_variables.keys()),
                tool_manifest_names=[tool.tool_name for tool in assembled_prompt.tool_manifest],
                assembled_at=datetime.now(UTC),
            )

            agent_started_at = datetime.now(UTC)
            invocation_result = agent_invoker.invoke(
                assembled_prompt=assembled_prompt,
                context=context,
            )
            agent_output = invocation_result.agent_output
            prompt_service.validate_output(
                bundle_id=context.bundle_id,
                bundle_version=context.bundle_version,
                payload=agent_output.structured_output,
            )
            if assembled_prompt.citation_policy_id != "none" and not agent_output.citations:
                raise OutputValidationError(
                    f"Citations are required for {assembled_prompt.bundle_id}@{assembled_prompt.bundle_version}"
                )

            state_patch = self.state_patch_builder(context, agent_output)
            state_after = str(state_patch.get("current_state", context.current_state))
            finished_at = datetime.now(UTC)
            checkpoint = WorkflowCheckpoint(
                workflow_run_id=context.workflow_run_id,
                workflow_type=context.workflow_type,
                checkpoint_seq=context.checkpoint_seq + 1,
                node_name=self.node_name,
                state_before=context.current_state,
                state_after=state_after,
                state_patch=state_patch,
                warning_codes=agent_output.warnings,
                recorded_at=finished_at,
            )
            replay_record = ReplayRecord(
                workflow_run_id=context.workflow_run_id,
                workflow_type=context.workflow_type,
                checkpoint_seq=checkpoint.checkpoint_seq,
                bundle_id=assembled_prompt.bundle_id,
                bundle_version=assembled_prompt.bundle_version,
                model_profile_id=assembled_prompt.model_profile_id,
                response_schema_ref=assembled_prompt.response_schema_ref,
                tool_manifest_names=[tool.tool_name for tool in assembled_prompt.tool_manifest],
                input_variable_names=sorted(assembled_prompt.resolved_variables.keys()),
                output_summary=agent_output.summary,
                recorded_at=finished_at,
            )
            emitted_outbox_events = [
                OutboxEvent(
                    event_id=str(uuid4()),
                    event_name=event_name,
                    workflow_run_id=context.workflow_run_id,
                    workflow_type=context.workflow_type,
                    node_name=self.node_name,
                    aggregate_type=context.aggregate_type or context.workflow_type,
                    aggregate_id=context.aggregate_id or context.workflow_run_id,
                    payload=state_patch,
                    emitted_at=finished_at,
                )
                for event_name in self.success_events
            ]
            if checkpoint_store is not None:
                checkpoint_store.save(checkpoint)
            if replay_store is not None:
                replay_store.save(replay_record)
            if event_emitter is not None:
                for event in emitted_outbox_events:
                    event_emitter.emit(event)
            agent_trace = AgentInvocationTrace(
                agent_name=assembled_prompt.agent_name,
                bundle_id=assembled_prompt.bundle_id,
                bundle_version=assembled_prompt.bundle_version,
                model_profile_id=assembled_prompt.model_profile_id,
                response_schema_ref=assembled_prompt.response_schema_ref,
                status=agent_output.status,
                citation_count=len(agent_output.citations),
                warnings=agent_output.warnings,
                started_at=agent_started_at,
                finished_at=finished_at,
            )
            trace = NodeExecutionTrace(
                node_name=self.node_name,
                node_kind=self.node_kind,
                workflow_run_id=context.workflow_run_id,
                workflow_type=context.workflow_type,
                state_before=context.current_state,
                state_after=state_after,
                emitted_events=self.success_events,
                prompt_trace=prompt_trace,
                agent_trace=agent_trace,
                tool_traces=invocation_result.tool_traces,
                started_at=started_at,
                finished_at=finished_at,
            )
            return NodeExecutionResult(
                state_patch=state_patch,
                emitted_events=self.success_events,
                warnings=agent_output.warnings,
                trace=trace,
                agent_output=agent_output,
                checkpoint=checkpoint,
                replay_record=replay_record,
                emitted_outbox_events=emitted_outbox_events,
                tool_results=[result.normalized_payload for result in invocation_result.tool_results],
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (OutputValidationError, NodeExecutionError)):
                raise
            raise NodeExecutionError(
                f"Node execution failed for {self.node_name} ({context.bundle_id}@{context.bundle_version})"
            ) from exc
