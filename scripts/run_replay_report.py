from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.sample_payloads import (
    replay_baseline_capture_command,
    replay_evaluation_command,
    replay_run_command,
)


def main() -> None:
    service = build_app_service()
    try:
        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_run_command())
        executed = service.execute_replay_run(replay.replay_run_id)
        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
        )
        print(
            json.dumps(
                {
                    "baseline": baseline.model_dump(mode="json"),
                    "replay": replay.model_dump(mode="json"),
                    "executed": executed.model_dump(mode="json"),
                    "report": report.model_dump(mode="json"),
                },
                indent=2,
            )
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
