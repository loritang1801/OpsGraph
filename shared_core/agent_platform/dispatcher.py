from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import Field

from .events import EventEmitter, OutboxEvent
from .shared import SchemaModel


class StoredOutboxEvent(SchemaModel):
    event: OutboxEvent
    status: str = "pending"
    dispatched_at: datetime | None = None
    failure_message: str | None = None


class OutboxStore(Protocol):
    def append(self, event: OutboxEvent | dict) -> None: ...

    def list_pending(self) -> list[StoredOutboxEvent]: ...

    def mark_dispatched(self, event_id: str, dispatched_at: datetime) -> None: ...

    def mark_failed(self, event_id: str, failure_message: str) -> None: ...


class InMemoryOutboxStore:
    def __init__(self) -> None:
        self._events: dict[str, StoredOutboxEvent] = {}

    def append(self, event: OutboxEvent | dict) -> None:
        normalized = event if isinstance(event, OutboxEvent) else OutboxEvent.model_validate(event)
        self._events[normalized.event_id] = StoredOutboxEvent(event=normalized)

    def list_pending(self) -> list[StoredOutboxEvent]:
        return [event for event in self._events.values() if event.status == "pending"]

    def mark_dispatched(self, event_id: str, dispatched_at: datetime) -> None:
        stored = self._events[event_id]
        stored.status = "dispatched"
        stored.dispatched_at = dispatched_at
        stored.failure_message = None

    def mark_failed(self, event_id: str, failure_message: str) -> None:
        stored = self._events[event_id]
        stored.status = "failed"
        stored.failure_message = failure_message


class OutboxStoreEmitter:
    def __init__(self, store: OutboxStore) -> None:
        self.store = store

    def emit(self, event: OutboxEvent | dict) -> None:
        self.store.append(event)


class OutboxDispatchResult(SchemaModel):
    attempted_count: int = 0
    dispatched_count: int = 0
    failed_event_ids: list[str] = Field(default_factory=list)


class OutboxHandler(Protocol):
    def __call__(self, event: OutboxEvent) -> None: ...


class OutboxDispatcher:
    def __init__(self, store: OutboxStore, handler: OutboxHandler) -> None:
        self.store = store
        self.handler = handler

    def dispatch_pending(self, *, dispatched_at: datetime) -> OutboxDispatchResult:
        result = OutboxDispatchResult()
        for stored in self.store.list_pending():
            result.attempted_count += 1
            try:
                self.handler(stored.event)
            except Exception as exc:  # noqa: BLE001
                self.store.mark_failed(stored.event.event_id, str(exc))
                result.failed_event_ids.append(stored.event.event_id)
                continue
            self.store.mark_dispatched(stored.event.event_id, dispatched_at)
            result.dispatched_count += 1
        return result
