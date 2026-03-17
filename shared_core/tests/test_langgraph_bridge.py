from __future__ import annotations

import unittest

from agent_platform import (
    LangGraphBridge,
    PromptAssemblyService,
    PromptAssemblySources,
    SpecialistNodeHandler,
    StaticModelGateway,
    WorkflowStep,
    build_default_runtime_catalog,
)
from agent_platform.errors import LangGraphUnavailableError
from agent_platform.shared import SharedAgentOutputEnvelope


class LangGraphBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_step_callable_executes_without_langgraph_dependency(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="auditflow.writer",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Generated package narratives.",
                structured_output={
                    "narratives": [
                        {
                            "control_state_id": "control-state-1",
                            "narrative_type": "control_summary",
                            "content_markdown": "Grounded narrative.",
                            "citation_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}],
                        }
                    ]
                },
                citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
            ),
        )
        bridge = LangGraphBridge(self.prompt_service)
        step_callable = bridge.make_step_callable(
            workflow_run_id="wf-lg-1",
            workflow_type="auditflow_cycle",
            step=WorkflowStep(
                node_name="package_generation",
                node_kind="generation",
                bundle_id="auditflow.writer",
                bundle_version="2026-03-16.1",
                handler=SpecialistNodeHandler(
                    node_name="package_generation",
                    node_kind="generation",
                    state_patch_builder=lambda context, output: {"current_state": "exported"},
                ),
            ),
            source_builder=lambda state: PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "working_snapshot_version": 3},
                database={"accepted_mapping_refs": ["mapping-1"], "open_gap_refs": ["gap-1"]},
                trigger_payload={"export_scope": "cycle_package"},
            ),
            model_gateway=gateway,
        )

        next_state = step_callable({"current_state": "package_generation", "checkpoint_seq": 0})
        self.assertEqual(next_state["current_state"], "exported")
        self.assertEqual(next_state["checkpoint_seq"], 1)

    def test_build_sequential_graph_raises_when_langgraph_is_missing(self) -> None:
        bridge = LangGraphBridge(self.prompt_service)
        if bridge.is_available():
            self.skipTest("langgraph is installed in this environment")

        with self.assertRaises(LangGraphUnavailableError):
            bridge.build_sequential_graph(
                steps=[],
                source_builders={},
                workflow_run_id="wf-lg-2",
                workflow_type="auditflow_cycle",
            )


if __name__ == "__main__":
    unittest.main()
