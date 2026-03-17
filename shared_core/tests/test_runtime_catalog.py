from __future__ import annotations

import unittest

from pydantic import ValidationError

from agent_platform import build_default_runtime_catalog


class RuntimeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()

    def test_catalog_builds_and_validates(self) -> None:
        self.catalog.validate()

    def test_auditflow_mapper_bundle_is_wired_to_expected_policy(self) -> None:
        bundle = self.catalog.prompt_bundles.get("auditflow.mapper", "2026-03-16.1")
        self.assertEqual(bundle.tool_policy_id, "auditflow.mapper.policy")
        self.assertEqual(bundle.response_schema_ref, "auditflow.mapper.output.v1")

        policy = self.catalog.tool_policies.get(bundle.tool_policy_id, bundle.tool_policy_version)
        allowed_tool_names = [tool.tool_name for tool in policy.allowed_tools]
        self.assertEqual(
            allowed_tool_names,
            ["evidence.search", "control_catalog.lookup", "mapping.read_candidates"],
        )

    def test_opsgraph_comms_bundle_is_fact_bound(self) -> None:
        bundle = self.catalog.prompt_bundles.get("opsgraph.comms", "2026-03-16.1")
        self.assertEqual(bundle.citation_policy_id, "facts.required")
        self.assertEqual(bundle.model_profile_id, "generation.grounded")

    def test_runbook_advisor_output_requires_risk_level_and_evidence(self) -> None:
        schema = self.catalog.schemas.get("opsgraph.runbook_advisor.output.v1")
        valid_payload = {
            "recommendations": [
                {
                    "recommendation_type": "mitigate",
                    "risk_level": "high_risk",
                    "requires_approval": True,
                    "title": "Roll back deployment",
                    "instructions_markdown": "Rollback deployment 123.",
                    "evidence_refs": [{"kind": "deployment", "id": "deploy-123"}],
                }
            ]
        }
        schema.model_validate(valid_payload)

        with self.assertRaises(ValidationError):
            schema.model_validate(
                {
                    "recommendations": [
                        {
                            "recommendation_type": "mitigate",
                            "requires_approval": True,
                            "title": "Roll back deployment",
                            "instructions_markdown": "Rollback deployment 123.",
                            "evidence_refs": [],
                        }
                    ]
                }
            )

    def test_writer_output_requires_citation_refs(self) -> None:
        schema = self.catalog.schemas.get("auditflow.writer.output.v1")
        with self.assertRaises(ValidationError):
            schema.model_validate(
                {
                    "narratives": [
                        {
                            "control_state_id": "control-state-1",
                            "narrative_type": "control_summary",
                            "content_markdown": "Grounded content",
                            "citation_refs": [],
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
