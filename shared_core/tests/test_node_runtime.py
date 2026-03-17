from __future__ import annotations

import unittest

from agent_platform import (
    NodeExecutionContext,
    PromptAssemblyService,
    PromptAssemblySources,
    SpecialistNodeHandler,
    StaticAgentInvoker,
    build_default_runtime_catalog,
)
from agent_platform.errors import OutputValidationError
from agent_platform.shared import SharedAgentOutputEnvelope


class NodeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_specialist_node_handler_builds_trace_and_state_patch(self) -> None:
        handler = SpecialistNodeHandler(
            node_name="communicate",
            node_kind="generation",
            success_events=["opsgraph.comms.ready"],
            state_patch_builder=lambda context, output: {
                "current_state": "resolve",
                "comms_draft_ids": ["draft-1"],
            },
        )
        result = handler.execute(
            context=NodeExecutionContext(
                node_name="communicate",
                node_kind="generation",
                workflow_run_id="wf-1",
                workflow_type="opsgraph_incident",
                current_state="communicate",
                bundle_id="opsgraph.comms",
                bundle_version="2026-03-16.1",
                prompt_sources=PromptAssemblySources(
                    workflow_state={
                        "incident_id": "incident-1",
                        "current_fact_set_version": 4,
                    },
                    database={"confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}]},
                    trigger_payload={"target_channels": ["internal_slack"]},
                    computed={"channel_policy": {"external_requires_approval": True}},
                ),
            ),
            prompt_service=self.prompt_service,
            agent_invoker=StaticAgentInvoker(
                SharedAgentOutputEnvelope(
                    status="success",
                    summary="Generated draft.",
                    structured_output={
                        "drafts": [
                            {
                                "channel_type": "internal_slack",
                                "fact_set_version": 4,
                                "body_markdown": "Investigating elevated error rates.",
                                "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                            }
                        ]
                    },
                    citations=[{"kind": "incident_fact", "id": "fact-1"}],
                )
            ),
        )

        self.assertEqual(result.state_patch["current_state"], "resolve")
        self.assertEqual(result.trace.prompt_trace.bundle_id, "opsgraph.comms")
        self.assertEqual(result.trace.agent_trace.citation_count, 1)
        self.assertEqual(result.trace.emitted_events, ["opsgraph.comms.ready"])

    def test_specialist_node_handler_enforces_citations_for_grounded_bundle(self) -> None:
        handler = SpecialistNodeHandler(
            node_name="communicate",
            node_kind="generation",
        )
        with self.assertRaises(OutputValidationError):
            handler.execute(
                context=NodeExecutionContext(
                    node_name="communicate",
                    node_kind="generation",
                    workflow_run_id="wf-2",
                    workflow_type="opsgraph_incident",
                    current_state="communicate",
                    bundle_id="opsgraph.comms",
                    bundle_version="2026-03-16.1",
                    prompt_sources=PromptAssemblySources(
                        workflow_state={
                            "incident_id": "incident-1",
                            "current_fact_set_version": 4,
                        },
                        database={"confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}]},
                        trigger_payload={"target_channels": ["internal_slack"]},
                        computed={"channel_policy": {"external_requires_approval": True}},
                    ),
                ),
                prompt_service=self.prompt_service,
                agent_invoker=StaticAgentInvoker(
                    SharedAgentOutputEnvelope(
                        status="success",
                        summary="Generated draft.",
                        structured_output={
                            "drafts": [
                                {
                                    "channel_type": "internal_slack",
                                    "fact_set_version": 4,
                                    "body_markdown": "Investigating elevated error rates.",
                                    "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                                }
                            ]
                        },
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
