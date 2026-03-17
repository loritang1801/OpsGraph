from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import Field

from .shared import SchemaModel


class WorkflowCheckpoint(SchemaModel):
    workflow_run_id: str
    workflow_type: str
    checkpoint_seq: int
    node_name: str
    state_before: str
    state_after: str
    state_patch: dict[str, Any] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    recorded_at: datetime


class ReplayRecord(SchemaModel):
    workflow_run_id: str
    workflow_type: str
    checkpoint_seq: int
    bundle_id: str
    bundle_version: str
    model_profile_id: str
    response_schema_ref: str
    tool_manifest_names: list[str] = Field(default_factory=list)
    input_variable_names: list[str] = Field(default_factory=list)
    output_summary: str
    recorded_at: datetime


class CheckpointStore(Protocol):
    def save(self, checkpoint: WorkflowCheckpoint) -> None: ...


class ReplayStore(Protocol):
    def save(self, record: ReplayRecord) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints: list[WorkflowCheckpoint] = []

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        self.checkpoints.append(checkpoint)


class InMemoryReplayStore:
    def __init__(self) -> None:
        self.records: list[ReplayRecord] = []

    def save(self, record: ReplayRecord) -> None:
        self.records.append(record)
