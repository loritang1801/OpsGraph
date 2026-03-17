from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .checkpoints import CheckpointStore, ReplayStore
from .dispatcher import OutboxDispatcher, OutboxHandler, OutboxStore, OutboxStoreEmitter
from .events import EventEmitter
from .model_gateway import ModelGateway
from .persistence import WorkflowStateStore
from .replay import ReplayFixtureLoader
from .runtime import PromptAssemblyService, PromptAssemblySources
from .tool_executor import ToolExecutor
from .workflow_runner import PromptSourceBuilder, WorkflowRunResult, WorkflowRunner, WorkflowStep


class WorkflowExecutionService:
    def __init__(
        self,
        prompt_service: PromptAssemblyService,
        *,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        replay_loader: ReplayFixtureLoader | None = None,
        state_store: WorkflowStateStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        replay_store: ReplayStore | None = None,
        outbox_store: OutboxStore | None = None,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self.runner = WorkflowRunner(prompt_service)
        self.model_gateway = model_gateway
        self.tool_executor = tool_executor
        self.replay_loader = replay_loader
        self.state_store = state_store
        self.checkpoint_store = checkpoint_store
        self.replay_store = replay_store
        self.outbox_store = outbox_store
        self.event_emitter = event_emitter or (OutboxStoreEmitter(outbox_store) if outbox_store else None)

    def run_workflow(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        initial_state: dict[str, Any],
        steps: list[WorkflowStep],
        source_builders: dict[str, PromptSourceBuilder],
    ) -> WorkflowRunResult:
        return self.runner.run(
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            initial_state=initial_state,
            steps=steps,
            source_builders=source_builders,
            model_gateway=self.model_gateway,
            tool_executor=self.tool_executor,
            replay_loader=self.replay_loader,
            event_emitter=self.event_emitter,
            checkpoint_store=self.checkpoint_store,
            replay_store=self.replay_store,
            state_store=self.state_store,
        )

    def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        steps: list[WorkflowStep],
        source_builders: dict[str, PromptSourceBuilder],
    ) -> WorkflowRunResult:
        if self.state_store is None:
            raise ValueError("resume_workflow requires a state_store")
        record = self.state_store.load(workflow_run_id)
        remaining_steps = self._remaining_steps(
            steps=steps,
            current_state=str(record.state.get("current_state", "")),
        )
        return self.run_workflow(
            workflow_run_id=workflow_run_id,
            workflow_type=record.workflow_type,
            initial_state=record.state,
            steps=remaining_steps,
            source_builders=source_builders,
        )

    def replay_workflow(
        self,
        *,
        workflow_run_id: str,
        workflow_type: str,
        initial_state: dict[str, Any],
        steps: list[WorkflowStep],
        source_builders: dict[str, PromptSourceBuilder],
    ) -> WorkflowRunResult:
        if self.replay_loader is None:
            raise ValueError("replay_workflow requires a replay_loader")
        return self.runner.run(
            workflow_run_id=workflow_run_id,
            workflow_type=workflow_type,
            initial_state=initial_state,
            steps=steps,
            source_builders=source_builders,
            replay_loader=self.replay_loader,
            event_emitter=self.event_emitter,
            checkpoint_store=self.checkpoint_store,
            replay_store=self.replay_store,
            state_store=self.state_store,
        )

    def load_workflow_state(self, workflow_run_id: str) -> dict[str, Any]:
        if self.state_store is None:
            raise ValueError("load_workflow_state requires a state_store")
        return self.state_store.load(workflow_run_id).state

    def dispatch_outbox(self, handler: OutboxHandler) -> Any:
        if self.outbox_store is None:
            raise ValueError("dispatch_outbox requires an outbox_store")
        dispatcher = OutboxDispatcher(self.outbox_store, handler)
        return dispatcher.dispatch_pending(dispatched_at=datetime.now(UTC))

    @staticmethod
    def _remaining_steps(
        *,
        steps: list[WorkflowStep],
        current_state: str,
    ) -> list[WorkflowStep]:
        if not current_state:
            return steps
        for index, step in enumerate(steps):
            if step.node_name == current_state:
                return steps[index:]
        return steps
