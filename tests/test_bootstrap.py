from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import (
    build_api_service,
    build_fastapi_app,
    build_replay_worker,
    build_replay_worker_supervisor,
    list_supported_workflows,
)
from opsgraph_app.sample_payloads import incident_response_request, replay_run_command
from opsgraph_app.shared_runtime import load_shared_agent_platform

_AP = load_shared_agent_platform()


class OpsGraphBootstrapTests(unittest.TestCase):
    def test_lists_product_workflows_only(self) -> None:
        self.assertEqual(
            list_supported_workflows(),
            ("opsgraph_incident_response", "opsgraph_retrospective"),
        )

    def test_build_api_service_and_run_incident_demo(self) -> None:
        api_service = build_api_service()
        self.addCleanup(api_service.close)
        self.assertEqual(len(api_service.list_workflows()), 2)
        result = api_service.start_workflow(incident_response_request(workflow_run_id="opsgraph-test-1"))
        self.assertEqual(result.workflow_name, "opsgraph_incident_response")
        self.assertEqual(result.current_state, "resolve")

    def test_build_fastapi_app_or_raise_expected_error(self) -> None:
        try:
            app = build_fastapi_app()
        except Exception as exc:
            self.assertEqual(exc.__class__.__name__, "FastAPIUnavailableError")
        else:
            service = _AP.assert_managed_app_service(self, app, state_attr="opsgraph_service")
            self.assertTrue(hasattr(app, "routes"))

    def test_build_replay_worker_and_dispatch_jobs(self) -> None:
        worker = build_replay_worker()
        self.addCleanup(worker.app_service.close)

        worker.app_service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-bootstrap-worker-v1"),
            idempotency_key="opsgraph-bootstrap-worker-1",
        )
        result = worker.dispatch_once()
        replays = worker.app_service.list_replays("ops-ws-1")

        self.assertEqual(result.dispatched_count, 1)
        self.assertTrue(any(item.status == "completed" for item in replays))

    def test_build_replay_worker_supervisor_and_emit_idle_heartbeat(self) -> None:
        supervisor = build_replay_worker_supervisor()
        self.addCleanup(supervisor.worker.app_service.close)

        heartbeats = supervisor.run(
            poll_interval_seconds=0,
            max_iterations=1,
            max_idle_polls=1,
            heartbeat_every_iterations=1,
        )

        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(heartbeats[0].status, "idle")

    def test_shared_runtime_defaults_to_vendored_shared_core(self) -> None:
        with patch.dict("os.environ", {"OPSGRAPH_SHARED_CORE_SOURCE": ""}, clear=False):
            shared_platform = load_shared_agent_platform()

        self.assertEqual(shared_platform.__name__, "shared_core.agent_platform")


if __name__ == "__main__":
    unittest.main()
