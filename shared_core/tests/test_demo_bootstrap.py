from __future__ import annotations

import unittest

from agent_platform import build_demo_runtime_components


class DemoBootstrapTests(unittest.TestCase):
    def test_demo_components_build_and_expose_workflows(self) -> None:
        components = build_demo_runtime_components()
        runtime_stores = components.get("runtime_stores")
        if runtime_stores is not None and hasattr(runtime_stores, "dispose"):
            self.addCleanup(runtime_stores.dispose)
        workflow_names = [definition.workflow_name for definition in components["workflow_registry"].list()]

        self.assertEqual(len(workflow_names), 4)
        self.assertIn("auditflow_cycle_processing", workflow_names)
        self.assertIn("opsgraph_incident_response", workflow_names)


if __name__ == "__main__":
    unittest.main()
