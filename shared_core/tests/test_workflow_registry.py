from __future__ import annotations

import unittest

from agent_platform import build_workflow_registry


class WorkflowRegistryTests(unittest.TestCase):
    def test_registry_contains_expected_workflows(self) -> None:
        registry = build_workflow_registry()
        names = [definition.workflow_name for definition in registry.list()]

        self.assertEqual(
            names,
            [
                "auditflow_cycle_processing",
                "auditflow_export_generation",
                "opsgraph_incident_response",
                "opsgraph_retrospective",
            ],
        )

    def test_registry_returns_expected_definition(self) -> None:
        registry = build_workflow_registry()
        definition = registry.get("opsgraph_incident_response")

        self.assertEqual(definition.workflow_type, "opsgraph_incident")
        self.assertEqual(
            [step.node_name for step in definition.steps],
            ["triage", "hypothesize", "advise", "communicate"],
        )


if __name__ == "__main__":
    unittest.main()
