from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import Field

from .shared import SchemaModel


class OutboxEvent(SchemaModel):
    event_id: str
    event_name: str
    workflow_run_id: str
    workflow_type: str
    node_name: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    emitted_at: datetime


class EventEmitter(Protocol):
    def emit(self, event: OutboxEvent) -> None: ...


class InMemoryEventEmitter:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    def emit(self, event: OutboxEvent) -> None:
        self.events.append(event)
