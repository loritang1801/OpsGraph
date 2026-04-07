from __future__ import annotations

import argparse
import json
from uuid import uuid4

from _local_runtime import ensure_src_on_path, resolve_database_url

ensure_src_on_path()

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.sample_payloads import incident_response_command, retrospective_command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OpsGraph demo workflows against a local database.")
    parser.add_argument("--database-url", help="Optional SQLAlchemy database URL.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    suffix = uuid4().hex[:8]
    service = build_app_service(database_url=resolve_database_url(args.database_url))
    try:
        response = service.respond_to_incident(
            incident_response_command(workflow_run_id=f"opsgraph-demo-incident-{suffix}")
        )
        retrospective = service.build_retrospective(
            retrospective_command(workflow_run_id=f"opsgraph-demo-retro-{suffix}")
        )
        print(
            json.dumps(
                {
                    "incident_response": response.model_dump(),
                    "retrospective": retrospective.model_dump(),
                },
                indent=2,
            )
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
