from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.sample_payloads import incident_response_command, retrospective_command


def main() -> None:
    service = build_app_service()
    response = service.respond_to_incident(incident_response_command(workflow_run_id="opsgraph-demo-1"))
    retrospective = service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-demo-2"))
    print(json.dumps({"incident_response": response.model_dump(), "retrospective": retrospective.model_dump()}, indent=2))


if __name__ == "__main__":
    main()
