from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_api_service, build_fastapi_app, list_supported_workflows
from opsgraph_app.sample_payloads import incident_response_request


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
            self.assertTrue(hasattr(app, "routes"))


if __name__ == "__main__":
    unittest.main()
