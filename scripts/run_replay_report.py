from __future__ import annotations

import argparse
import json
from _local_runtime import ensure_src_on_path, resolve_database_url

ensure_src_on_path()

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.sample_payloads import (
    replay_baseline_capture_command,
    replay_evaluation_command,
    replay_run_command,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an OpsGraph replay report against a local database.")
    parser.add_argument("--database-url", help="Optional SQLAlchemy database URL.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service = build_app_service(database_url=resolve_database_url(args.database_url))
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
                    "summary": {
                        "status": report.status,
                        "score": report.score,
                        "mismatch_count": report.mismatch_count,
                        "node_match_rate": report.node_match_rate,
                        "latency_regression_count": report.latency_regression_count,
                        "latency_improvement_count": report.latency_improvement_count,
                    },
                    "artifacts": {
                        "json_report_path": report.report_artifact_path,
                        "markdown_report_path": report.markdown_report_path,
                        "csv_report_path": report.csv_report_path,
                    },
                },
                indent=2,
            )
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
