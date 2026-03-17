from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .errors import RegistryLookupError
from .shared import SchemaModel


class WorkflowStateRecord(SchemaModel):
    workflow_run_id: str
    workflow_type: str
    checkpoint_seq: int
    state: dict[str, Any]
    updated_at: datetime


class WorkflowStateStore(Protocol):
    def save(self, record: WorkflowStateRecord) -> None: ...

    def load(self, workflow_run_id: str) -> WorkflowStateRecord: ...


class InMemoryWorkflowStateStore:
    def __init__(self) -> None:
        self._records: dict[str, WorkflowStateRecord] = {}

    def save(self, record: WorkflowStateRecord) -> None:
        self._records[record.workflow_run_id] = record

    def load(self, workflow_run_id: str) -> WorkflowStateRecord:
        if workflow_run_id not in self._records:
            raise RegistryLookupError(f"Unknown workflow state record: {workflow_run_id}")
        return self._records[workflow_run_id]
