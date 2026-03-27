from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_replay_worker
from opsgraph_app.sample_payloads import replay_run_command
from opsgraph_app.worker import OpsGraphReplayWorkerSupervisor, ReplayWorkerDispatchResult


class OpsGraphReplayWorkerTests(unittest.TestCase):
    def test_worker_dispatch_once_processes_queued_replay(self) -> None:
        worker = build_replay_worker()
        self.addCleanup(worker.app_service.close)

        worker.app_service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-worker-v1"),
            idempotency_key="replay-worker-dispatch-1",
        )
        result = worker.dispatch_once()
        queued = worker.app_service.list_replays("ops-ws-1", status="queued")

        self.assertEqual(result.attempted_count, 1)
        self.assertEqual(result.dispatched_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.remaining_queued_count, 0)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].status, "completed")
        self.assertEqual(queued, [])

    def test_worker_run_polling_stops_after_idle_poll(self) -> None:
        worker = build_replay_worker()
        self.addCleanup(worker.app_service.close)

        worker.app_service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-worker-poll-v1"),
            idempotency_key="replay-worker-poll-1",
        )
        results = worker.run_polling(
            poll_interval_seconds=0,
            max_iterations=5,
            max_idle_polls=1,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].attempted_count, 1)
        self.assertEqual(results[0].dispatched_count, 1)
        self.assertEqual(results[1].attempted_count, 0)
        self.assertEqual(results[1].remaining_queued_count, 0)

    def test_supervisor_retries_transient_failure_and_emits_heartbeat(self) -> None:
        emitted: list[object] = []
        sleeps: list[float] = []
        timestamps = iter(
            (
                datetime(2026, 3, 27, 9, 0, tzinfo=UTC),
                datetime(2026, 3, 27, 9, 0, 5, tzinfo=UTC),
            )
        )

        class FakeWorker:
            def __init__(self) -> None:
                self.attempts = 0
                self.app_service = SimpleNamespace()

            def dispatch_once(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("transient failure")
                return ReplayWorkerDispatchResult(
                    attempted_count=1,
                    dispatched_count=1,
                    failed_count=0,
                    skipped_count=0,
                    queued_count=1,
                    remaining_queued_count=0,
                    items=[],
                )

            @staticmethod
            def record_heartbeat(_heartbeat) -> None:
                return None

        supervisor = OpsGraphReplayWorkerSupervisor(
            FakeWorker(),
            sleep_fn=sleeps.append,
            now_fn=lambda: next(timestamps),
        )

        heartbeats = supervisor.run(
            poll_interval_seconds=0,
            max_iterations=2,
            max_consecutive_failures=2,
            failure_backoff_seconds=0.25,
            heartbeat_callback=emitted.append,
        )

        self.assertEqual([heartbeat.status for heartbeat in heartbeats], ["retrying", "active"])
        self.assertEqual(heartbeats[0].consecutive_failures, 1)
        self.assertEqual(heartbeats[1].dispatched_count, 1)
        self.assertEqual(heartbeats[1].remaining_queued_count, 0)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(len(emitted), 2)

    def test_supervisor_records_last_heartbeat_in_repository(self) -> None:
        worker = build_replay_worker()
        self.addCleanup(worker.app_service.close)

        worker.app_service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-worker-heartbeat-v1"),
            idempotency_key="replay-worker-heartbeat-1",
        )
        heartbeats = worker.build_supervisor().run(
            poll_interval_seconds=0,
            max_iterations=2,
            max_idle_polls=1,
            heartbeat_every_iterations=1,
        )
        status = worker.app_service.repository.get_replay_worker_status("ops-ws-1")

        self.assertEqual([heartbeat.status for heartbeat in heartbeats], ["active", "idle"])
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.workspace_id, "ops-ws-1")
        self.assertEqual(status.status, "idle")
        self.assertEqual(status.remaining_queued_count, 0)
        history = worker.app_service.repository.list_replay_worker_history("ops-ws-1", limit=5)
        self.assertEqual([item.status for item in history], ["idle", "active"])

    def test_supervisor_raises_after_reaching_failure_threshold(self) -> None:
        emitted: list[object] = []
        sleeps: list[float] = []
        timestamps = iter(
            (
                datetime(2026, 3, 27, 9, 5, tzinfo=UTC),
                datetime(2026, 3, 27, 9, 5, 5, tzinfo=UTC),
            )
        )

        class FailingWorker:
            app_service = SimpleNamespace()

            @staticmethod
            def dispatch_once():
                raise RuntimeError("persistent failure")

            @staticmethod
            def record_heartbeat(_heartbeat) -> None:
                return None

        supervisor = OpsGraphReplayWorkerSupervisor(
            FailingWorker(),
            sleep_fn=sleeps.append,
            now_fn=lambda: next(timestamps),
        )

        with self.assertRaisesRegex(RuntimeError, "persistent failure"):
            supervisor.run(
                poll_interval_seconds=0,
                max_iterations=5,
                max_consecutive_failures=2,
                failure_backoff_seconds=0.5,
                heartbeat_callback=emitted.append,
            )

        self.assertEqual([heartbeat.status for heartbeat in emitted], ["retrying", "failed"])
        self.assertEqual(sleeps, [0.5])


if __name__ == "__main__":
    unittest.main()
