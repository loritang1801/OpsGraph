from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_replay_worker
from opsgraph_app.sample_payloads import replay_run_command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued OpsGraph replay runs.")
    parser.add_argument("--workspace-id", default="ops-ws-1")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll", action="store_true", help="Run polling mode instead of a single batch.")
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Remove the iteration cap. Combine with --max-idle-polls 0 for a long-running worker.",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds.")
    parser.add_argument("--iterations", type=int, default=1, help="Maximum polling iterations.")
    parser.add_argument(
        "--max-idle-polls",
        type=int,
        default=1,
        help="Stop after this many idle polls. Use 0 to disable the idle stop condition.",
    )
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="Run the worker under a supervisor loop with retries and heartbeat output.",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help="Supervisor-only: stop after this many consecutive dispatch exceptions.",
    )
    parser.add_argument(
        "--failure-backoff",
        type=float,
        default=5.0,
        help="Supervisor-only: seconds to wait before retrying after a dispatch exception.",
    )
    parser.add_argument(
        "--heartbeat-every",
        type=int,
        default=1,
        help="Supervisor-only: emit an idle heartbeat every N iterations.",
    )
    parser.add_argument("--seed-run", action="store_true")
    parser.add_argument("--model-bundle-version", default="opsgraph-v1.2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    worker = build_replay_worker(
        workspace_id=args.workspace_id,
        limit=args.limit,
    )
    try:
        seeded = None
        if args.seed_run:
            seeded = worker.app_service.start_replay_run(
                replay_run_command(model_bundle_version=args.model_bundle_version),
                idempotency_key=f"replay-worker-seed-{args.model_bundle_version}",
            )
        max_iterations = None if args.forever else args.iterations
        max_idle_polls = None if args.max_idle_polls <= 0 else args.max_idle_polls
        if args.supervise:
            supervisor = worker.build_supervisor()

            def emit_heartbeat(heartbeat) -> None:
                print(json.dumps(heartbeat.to_dict()))

            heartbeats = supervisor.run(
                poll_interval_seconds=args.interval,
                max_iterations=max_iterations,
                max_idle_polls=max_idle_polls,
                max_consecutive_failures=args.max_consecutive_failures,
                failure_backoff_seconds=args.failure_backoff,
                heartbeat_every_iterations=args.heartbeat_every,
                heartbeat_callback=emit_heartbeat,
            )
            payload = {
                "seeded": None if seeded is None else seeded.model_dump(mode="json"),
                "heartbeat_count": len(heartbeats),
            }
        elif args.poll:
            results = worker.run_polling(
                poll_interval_seconds=args.interval,
                max_iterations=max_iterations,
                max_idle_polls=max_idle_polls,
            )
            payload = {
                "seeded": None if seeded is None else seeded.model_dump(mode="json"),
                "results": [result.to_dict() for result in results],
            }
        else:
            result = worker.dispatch_once()
            payload = {
                "seeded": None if seeded is None else seeded.model_dump(mode="json"),
                "result": result.to_dict(),
            }
        print(json.dumps(payload, indent=2))
    finally:
        worker.app_service.close()


if __name__ == "__main__":
    main()
