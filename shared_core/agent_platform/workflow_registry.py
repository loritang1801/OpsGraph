from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .errors import RegistryLookupError
from .workflow_runner import PromptSourceBuilder, WorkflowStep

InitialStateBuilder = Callable[[str, dict[str, Any], dict[str, Any] | None], dict[str, Any]]


@dataclass(slots=True)
class WorkflowDefinition:
    workflow_name: str
    workflow_type: str
    description: str
    steps: list[WorkflowStep]
    source_builders: dict[str, PromptSourceBuilder]
    initial_state_builder: InitialStateBuilder


class WorkflowRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        if definition.workflow_name in self._definitions:
            raise ValueError(f"Duplicate workflow definition: {definition.workflow_name}")
        self._definitions[definition.workflow_name] = definition

    def get(self, workflow_name: str) -> WorkflowDefinition:
        if workflow_name not in self._definitions:
            raise RegistryLookupError(f"Unknown workflow definition: {workflow_name}")
        return self._definitions[workflow_name]

    def list(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())
