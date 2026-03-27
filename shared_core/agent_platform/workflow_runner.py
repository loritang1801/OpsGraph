from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Callable

from pydantic import Field

from .checkpoints import CheckpointStore, ReplayStore
from .events import EventEmitter
from .model_gateway import GatewayAgentInvoker, ModelGateway
from .node_runtime import NodeExecutionContext, NodeExecutionResult, SpecialistNodeHandler
from .persistence import WorkflowStateRecord, WorkflowStateStore
from .replay import ReplayFixtureLoader
from .runtime import PromptAssemblyService, PromptAssemblySources
from .shared import SchemaModel
from .tool_executor import ToolExecutor
from .traces import NodeExecutionTrace


PromptSourceBuilder = Callable[[dict[str, Any]], PromptAssemblySources]


class WorkflowStep(SchemaModel):
    node_name: str
    node_kind: str
    bundle_id: str
    bundle_version: str
    handler: SpecialistNodeHandler
    source_builder_name: str | None = None

    model_config = SchemaModel.model_config | {"arbitrary_types_allowed": True}


class WorkflowRunResult(SchemaModel):
    workflow_run_id: str
    workflow_type: str
    final_state: dict[str, Any]
    step_results: list[NodeExecutionResult] = Field(default_factory=list)
    traces: list[NodeExecutionTrace] = Field(default_factory=list)

    model_config = SchemaModel.model_config | {"arbitrary_types_allowed": True}


class WorkflowRunner:
    def __init__(self, prompt_service: PromptAssemblyService) -> None:
        self.prompt_service = prompt_service

    def run(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        initial_state: dict[str, Any],
        steps: list[WorkflowStep],
        source_builders: dict[str, PromptSourceBuilder],
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        replay_loader: ReplayFixtureLoader | None = None,
        event_emitter: EventEmitter | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
        state_store: WorkflowStateStore | None = None,
    ) -> WorkflowRunResult:
        state = deepcopy(initial_state)
        step_results: list[NodeExecutionResult] = []
        traces: list[NodeExecutionTrace] = []

        for step in steps:
            builder_key = step.source_builder_name or step.node_name
            result = self.execute_step(
                workflow_run_id=workflow_run_id,
                workflow_type=workflow_type,
                state=state,
                step=step,
                source_builder=source_builders[builder_key],
                model_gateway=model_gateway,
                tool_executor=tool_executor,
                replay_loader=replay_loader,
                event_emitter=event_emitter,
                checkpoint_store=checkpoint_store,
                replay_store=replay_store,
                state_store=state_store,
            )
            state.update(result.state_patch)
            if result.checkpoint is not None:
                state["checkpoint_seq"] = result.checkpoint.checkpoint_seq
            state["current_state"] = result.state_patch.get("current_state", state.get("current_state"))
            step_results.append(result)
            traces.append(result.trace)

        return WorkflowRunResult(
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            final_state=state,
            step_results=step_results,
            traces=traces,
        )

    def execute_step(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        state: dict[str, Any],
        step: WorkflowStep,
        source_builder: PromptSourceBuilder,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        replay_loader: ReplayFixtureLoader | None = None,
        event_emitter: EventEmitter | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
        state_store: WorkflowStateStore | None = None,
    ) -> NodeExecutionResult:
        context = NodeExecutionContext(
            node_name=step.node_name,
            node_kind=step.node_kind,
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            organization_id=str(state.get("organization_id", "unknown-org")),
            workspace_id=str(state.get("workspace_id", "unknown-workspace")),
            user_id=(
                str(state["user_id"])
                if state.get("user_id") not in {None, ""}
                else None
            ),
            role=(
                str(state["role"])
                if state.get("role") not in {None, ""}
                else None
            ),
            session_id=(
                str(state["session_id"])
                if state.get("session_id") not in {None, ""}
                else None
            ),
            subject_type=str(state.get("subject_type", workflow_type)),
            subject_id=str(state.get("subject_id", workflow_run_id)),
            current_state=str(state.get("current_state", step.node_name)),
            checkpoint_seq=int(state.get("checkpoint_seq", 0)),
            aggregate_type=state.get("aggregate_type"),
            aggregate_id=state.get("aggregate_id"),
            bundle_id=step.bundle_id,
            bundle_version=step.bundle_version,
            prompt_sources=source_builder(state),
            metadata={"runner_step": step.node_name},
        )

        if replay_loader is not None:
            fixture_key = replay_loader.make_fixture_key(
                workflow_run_id=workflow_run_id,
                checkpoint_seq=context.checkpoint_seq + 1,
                node_name=step.node_name,
            )
            agent_invoker = replay_loader.build_invoker(fixture_key)
        elif model_gateway is not None:
            agent_invoker = GatewayAgentInvoker(model_gateway, tool_executor=tool_executor)
        else:
            raise ValueError("WorkflowRunner requires either model_gateway or replay_loader")

        result = step.handler.execute(
            context=context,
            prompt_service=self.prompt_service,
            agent_invoker=agent_invoker,
            event_emitter=event_emitter,
            checkpoint_store=checkpoint_store,
            replay_store=replay_store,
        )
        if state_store is not None:
            persisted_state = deepcopy(state)
            persisted_state.update(result.state_patch)
            if result.checkpoint is not None:
                persisted_state["checkpoint_seq"] = result.checkpoint.checkpoint_seq
            persisted_state["current_state"] = result.state_patch.get(
                "current_state",
                persisted_state.get("current_state"),
            )
            state_store.save(
                WorkflowStateRecord(
                    workflow_run_id=workflow_run_id,
                    workflow_type=workflow_type,
                    checkpoint_seq=int(persisted_state.get("checkpoint_seq", 0)),
                    state=persisted_state,
                    updated_at=datetime.now(UTC),
                )
            )
        return result
