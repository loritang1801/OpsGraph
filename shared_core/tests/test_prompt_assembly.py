from __future__ import annotations

import unittest

from agent_platform import PromptAssemblyService, PromptAssemblySources, build_default_runtime_catalog
from agent_platform.errors import OutputValidationError, PromptAssemblyError


class PromptAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.service = PromptAssemblyService(self.catalog)

    def test_writer_prompt_assembly_includes_snapshot_and_tools(self) -> None:
        assembled = self.service.assemble(
            bundle_id="auditflow.writer",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={
                    "audit_cycle_id": "cycle-1",
                    "working_snapshot_version": 3,
                },
                database={
                    "accepted_mapping_refs": ["mapping-1"],
                    "open_gap_refs": ["gap-1"],
                },
                trigger_payload={"export_scope": "cycle_package"},
            ),
        )

        self.assertEqual(assembled.bundle_id, "auditflow.writer")
        self.assertEqual(assembled.resolved_variables["working_snapshot_version"], 3)
        self.assertEqual(
            [tool.tool_name for tool in assembled.tool_manifest],
            ["narrative.snapshot_read", "control_catalog.lookup", "export.snapshot_validate"],
        )

    def test_mapper_prompt_assembly_includes_memory_context(self) -> None:
        assembled = self.service.assemble(
            bundle_id="auditflow.mapper",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={
                    "audit_cycle_id": "cycle-1",
                    "evidence_item_id": "evidence-1",
                },
                retrieval={
                    "evidence_chunk_refs": [
                        {"kind": "artifact_chunk_preview", "artifact_id": "artifact-1", "chunk_index": 0}
                    ]
                },
                database={
                    "in_scope_controls": [{"control_code": "CC6.1", "title": "Access review"}],
                    "framework_name": "SOC2",
                },
                memory={
                    "accepted_pattern_memories": [
                        {"memory_id": "memory-1", "decision": "accept", "control_code": "CC6.1"}
                    ]
                },
            ),
        )

        self.assertEqual(assembled.resolved_variables["framework_name"], "SOC2")
        self.assertEqual(len(assembled.resolved_variables["accepted_pattern_memories"]), 1)
        memory_part = next(part for part in assembled.parts if part.name == "memory_context")
        self.assertEqual(memory_part.variables["accepted_pattern_memories"][0]["control_code"], "CC6.1")

    def test_prompt_assembly_rejects_missing_required_variable(self) -> None:
        with self.assertRaises(PromptAssemblyError):
            self.service.assemble(
                bundle_id="opsgraph.comms",
                bundle_version="2026-03-16.1",
                sources=PromptAssemblySources(
                    workflow_state={
                        "incident_id": "incident-1",
                        "current_fact_set_version": 4,
                    },
                    database={"confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}]},
                ),
            )

    def test_comms_prompt_assembly_is_fact_bound(self) -> None:
        assembled = self.service.assemble(
            bundle_id="opsgraph.comms",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={
                    "incident_id": "incident-1",
                    "current_fact_set_version": 4,
                },
                database={"confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}]},
                trigger_payload={"target_channels": ["internal_slack"]},
                computed={"channel_policy": {"external_requires_approval": True}},
            ),
        )

        self.assertEqual(assembled.citation_policy_id, "facts.required")
        self.assertEqual(assembled.resolved_variables["current_fact_set_version"], 4)
        self.assertIn("comms.channel_preview", [tool.tool_name for tool in assembled.tool_manifest])

    def test_output_validation_raises_for_invalid_payload(self) -> None:
        with self.assertRaises(OutputValidationError):
            self.service.validate_output(
                bundle_id="auditflow.writer",
                bundle_version="2026-03-16.1",
                payload={
                    "narratives": [
                        {
                            "control_state_id": "control-1",
                            "narrative_type": "control_summary",
                            "content_markdown": "Missing citations",
                            "citation_refs": [],
                        }
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()
