from __future__ import annotations

from typing import Any, Callable

from .errors import LangGraphUnavailableError
from .events import EventEmitter
from .model_gateway import ModelGateway
from .persistence import WorkflowStateStore
from .replay import ReplayFixtureLoader
from .runtime import PromptAssemblyService, PromptAssemblySources
from .tool_executor import ToolExecutor
from .workflow_runner import PromptSourceBuilder, WorkflowRunner, WorkflowStep
from .checkpoints import CheckpointStore, ReplayStore


class LangGraphBridge:
    def __init__(self, prompt_service: PromptAssemblyService) -> None:
        self.runner = WorkflowRunner(prompt_service)

    @staticmethod
    def is_available() -> bool:
        try:
            import langgraph  # noqa: F401
        except ImportError:
            return False
        return True

    def make_step_callable(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        step: WorkflowStep,
        source_builder: PromptSourceBuilder,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        replay_loader: ReplayFixtureLoader | None = None,
        event_emitter: EventEmitter | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
        state_store: WorkflowStateStore | None = None,
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def _call(state: dict[str, Any]) -> dict[str, Any]:
            result = self.runner.execute_step(
                workflow_run_id=workflow_run_id,
                workflow_type=workflow_type,
                state=state,
                step=step,
                source_builder=source_builder,
                model_gateway=model_gateway,
                tool_executor=tool_executor,
                replay_loader=replay_loader,
                event_emitter=event_emitter,
                checkpoint_store=checkpoint_store,
                replay_store=replay_store,
                state_store=state_store,
            )
            next_state = dict(state)
            next_state.update(result.state_patch)
            if result.checkpoint is not None:
                next_state["checkpoint_seq"] = result.checkpoint.checkpoint_seq
            next_state["current_state"] = result.state_patch.get("current_state", next_state.get("current_state"))
            return next_state

        return _call

    def build_sequential_graph(
        self,
        *,
        steps: list[WorkflowStep],
        source_builders: dict[str, PromptSourceBuilder],
        workflow_run_id: str,
        workflow_type: str,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        replay_loader: ReplayFixtureLoader | None = None,
        event_emitter: EventEmitter | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
        state_store: WorkflowStateStore | None = None,
    ) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise LangGraphUnavailableError("langgraph is not installed") from exc

        graph = StateGraph(dict)
        previous = START
        for step in steps:
            builder_key = step.source_builder_name or step.node_name
            if builder_key not in source_builders:
                raise KeyError(f"Missing prompt source builder for step {step.node_name}")
            graph.add_node(
                step.node_name,
                self.make_step_callable(
                    workflow_run_id=workflow_run_id,
                    workflow_type=workflow_type,
                    step=step,
                    source_builder=source_builders[builder_key],
                    model_gateway=model_gateway,
                    tool_executor=tool_executor,
                    replay_loader=replay_loader,
                    event_emitter=event_emitter,
                    checkpoint_store=checkpoint_store,
                    replay_store=replay_store,
                    state_store=state_store,
                ),
            )
            graph.add_edge(previous, step.node_name)
            previous = step.node_name
        graph.add_edge(previous, END)
        return graph
