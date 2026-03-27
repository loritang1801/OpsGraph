from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from typing import Callable

from .api_models import ReplayRunSummary


@dataclass(slots=True)
class ReplayWorkerDispatchResult:
    attempted_count: int
    dispatched_count: int
    failed_count: int
    skipped_count: int
    queued_count: int
    remaining_queued_count: int
    items: list[ReplayRunSummary]

    def to_dict(self) -> dict[str, object]:
        return {
            "attempted_count": self.attempted_count,
            "dispatched_count": self.dispatched_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "queued_count": self.queued_count,
            "remaining_queued_count": self.remaining_queued_count,
            "items": [item.model_dump(mode="json") for item in self.items],
        }


@dataclass(slots=True)
class ReplayWorkerHeartbeat:
    iteration: int
    status: str
    attempted_count: int
    dispatched_count: int
    failed_count: int
    skipped_count: int
    idle_polls: int
    consecutive_failures: int
    remaining_queued_count: int
    emitted_at: datetime
    error_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        timestamp = self.emitted_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return {
            "iteration": self.iteration,
            "status": self.status,
            "attempted_count": self.attempted_count,
            "dispatched_count": self.dispatched_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "idle_polls": self.idle_polls,
            "consecutive_failures": self.consecutive_failures,
            "remaining_queued_count": self.remaining_queued_count,
            "emitted_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "error_message": self.error_message,
        }


class OpsGraphReplayWorkerSupervisor:
    def __init__(
        self,
        worker: "OpsGraphReplayWorker",
        *,
        sleep_fn: Callable[[float], None] = sleep,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.worker = worker
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def run(
        self,
        *,
        poll_interval_seconds: float = 1.0,
        max_iterations: int | None = None,
        max_idle_polls: int | None = None,
        max_consecutive_failures: int = 3,
        failure_backoff_seconds: float = 5.0,
        heartbeat_every_iterations: int = 1,
        heartbeat_callback: Callable[[ReplayWorkerHeartbeat], None] | None = None,
    ) -> list[ReplayWorkerHeartbeat]:
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        if heartbeat_every_iterations < 1:
            raise ValueError("heartbeat_every_iterations must be >= 1")

        heartbeats: list[ReplayWorkerHeartbeat] = []
        iteration = 0
        idle_polls = 0
        consecutive_failures = 0
        normalized_max_idle_polls = None if max_idle_polls is not None and max_idle_polls <= 0 else max_idle_polls

        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                result = self.worker.dispatch_once()
            except Exception as exc:
                consecutive_failures += 1
                self._record_heartbeat(
                    heartbeats,
                    iteration=iteration,
                    status=("retrying" if consecutive_failures < max_consecutive_failures else "failed"),
                    attempted_count=0,
                    dispatched_count=0,
                    failed_count=1,
                    skipped_count=0,
                    idle_polls=idle_polls,
                    consecutive_failures=consecutive_failures,
                    remaining_queued_count=0,
                    error_message=str(exc),
                    heartbeat_callback=heartbeat_callback,
                )
                if consecutive_failures >= max_consecutive_failures:
                    raise
                if failure_backoff_seconds > 0:
                    self._sleep_fn(failure_backoff_seconds)
                continue

            consecutive_failures = 0
            idle_polls = idle_polls + 1 if result.attempted_count == 0 else 0
            status = "degraded" if result.failed_count > 0 else ("idle" if result.attempted_count == 0 else "active")
            should_emit_heartbeat = (
                result.attempted_count > 0
                or result.failed_count > 0
                or iteration % heartbeat_every_iterations == 0
                or (
                    normalized_max_idle_polls is not None
                    and idle_polls >= normalized_max_idle_polls
                )
            )
            if should_emit_heartbeat:
                self._record_heartbeat(
                    heartbeats,
                    iteration=iteration,
                    status=status,
                    attempted_count=result.attempted_count,
                    dispatched_count=result.dispatched_count,
                    failed_count=result.failed_count,
                    skipped_count=result.skipped_count,
                    idle_polls=idle_polls,
                    consecutive_failures=consecutive_failures,
                    remaining_queued_count=result.remaining_queued_count,
                    error_message=None,
                    heartbeat_callback=heartbeat_callback,
                )

            if normalized_max_idle_polls is not None and idle_polls >= normalized_max_idle_polls:
                break
            if max_iterations is not None and iteration >= max_iterations:
                break
            if poll_interval_seconds > 0:
                self._sleep_fn(poll_interval_seconds)

        return heartbeats

    def _record_heartbeat(
        self,
        heartbeats: list[ReplayWorkerHeartbeat],
        *,
        iteration: int,
        status: str,
        attempted_count: int,
        dispatched_count: int,
        failed_count: int,
        skipped_count: int,
        idle_polls: int,
        consecutive_failures: int,
        remaining_queued_count: int,
        error_message: str | None,
        heartbeat_callback: Callable[[ReplayWorkerHeartbeat], None] | None,
    ) -> ReplayWorkerHeartbeat:
        heartbeat = ReplayWorkerHeartbeat(
            iteration=iteration,
            status=status,
            attempted_count=attempted_count,
            dispatched_count=dispatched_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            idle_polls=idle_polls,
            consecutive_failures=consecutive_failures,
            remaining_queued_count=remaining_queued_count,
            emitted_at=self._now_fn(),
            error_message=error_message,
        )
        self.worker.record_heartbeat(heartbeat)
        heartbeats.append(heartbeat)
        if heartbeat_callback is not None:
            heartbeat_callback(heartbeat)
        return heartbeat


class OpsGraphReplayWorker:
    def __init__(
        self,
        app_service,
        *,
        workspace_id: str = "ops-ws-1",
        limit: int = 20,
    ) -> None:
        self.app_service = app_service
        self.workspace_id = workspace_id
        self.limit = limit

    def build_supervisor(
        self,
        *,
        sleep_fn: Callable[[float], None] = sleep,
        now_fn: Callable[[], datetime] | None = None,
    ) -> OpsGraphReplayWorkerSupervisor:
        return OpsGraphReplayWorkerSupervisor(
            self,
            sleep_fn=sleep_fn,
            now_fn=now_fn,
        )

    def dispatch_once(self) -> ReplayWorkerDispatchResult:
        batch = self.app_service.process_queued_replays(self.workspace_id, limit=self.limit)
        return ReplayWorkerDispatchResult(
            attempted_count=int(batch.processed_count),
            dispatched_count=int(batch.completed_count),
            failed_count=int(batch.failed_count),
            skipped_count=int(batch.skipped_count),
            queued_count=int(batch.queued_count),
            remaining_queued_count=int(batch.remaining_queued_count),
            items=list(batch.items),
        )

    def record_heartbeat(self, heartbeat: ReplayWorkerHeartbeat) -> None:
        self.app_service.repository.record_replay_worker_heartbeat(
            workspace_id=self.workspace_id,
            status=heartbeat.status,
            iteration=heartbeat.iteration,
            attempted_count=heartbeat.attempted_count,
            dispatched_count=heartbeat.dispatched_count,
            failed_count=heartbeat.failed_count,
            skipped_count=heartbeat.skipped_count,
            idle_polls=heartbeat.idle_polls,
            consecutive_failures=heartbeat.consecutive_failures,
            remaining_queued_count=heartbeat.remaining_queued_count,
            emitted_at=heartbeat.emitted_at,
            error_message=heartbeat.error_message,
        )

    def run_polling(
        self,
        *,
        poll_interval_seconds: float = 1.0,
        max_iterations: int | None = None,
        max_idle_polls: int | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> list[ReplayWorkerDispatchResult]:
        results: list[ReplayWorkerDispatchResult] = []
        iteration = 0
        idle_polls = 0
        normalized_max_idle_polls = None if max_idle_polls is not None and max_idle_polls <= 0 else max_idle_polls
        while max_iterations is None or iteration < max_iterations:
            result = self.dispatch_once()
            results.append(result)
            iteration += 1
            idle_polls = idle_polls + 1 if result.attempted_count == 0 else 0
            if normalized_max_idle_polls is not None and idle_polls >= normalized_max_idle_polls:
                break
            if max_iterations is not None and iteration >= max_iterations:
                break
            if poll_interval_seconds > 0:
                sleep_fn(poll_interval_seconds)
        return results
