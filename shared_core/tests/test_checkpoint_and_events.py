from __future__ import annotations

import unittest

from agent_platform import (
    InMemoryCheckpointStore,
    InMemoryEventEmitter,
    InMemoryReplayStore,
    NodeExecutionContext,
    PromptAssemblyService,
    PromptAssemblySources,
    SpecialistNodeHandler,
    StaticAgentInvoker,
    build_default_runtime_catalog,
)
from agent_platform.shared import SharedAgentOutputEnvelope


class CheckpointAndEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_specialist_node_persists_checkpoint_replay_and_events(self) -> None:
        checkpoint_store = InMemoryCheckpointStore()
        replay_store = InMemoryReplayStore()
        event_emitter = InMemoryEventEmitter()
        handler = SpecialistNodeHandler(
            node_name="package_generation",
            node_kind="generation",
            success_events=["auditflow.package.ready"],
            state_patch_builder=lambda context, output: {
                "current_state": "exported",
                "narrative_ids": ["narrative-1"],
            },
        )

        result = handler.execute(
            context=NodeExecutionContext(
                node_name="package_generation",
                node_kind="generation",
                workflow_run_id="wf-100",
                workflow_type="auditflow_cycle",
                current_state="package_generation",
                checkpoint_seq=4,
                aggregate_type="audit_cycle",
                aggregate_id="cycle-1",
                bundle_id="auditflow.writer",
                bundle_version="2026-03-16.1",
                prompt_sources=PromptAssemblySources(
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
            ),
            prompt_service=self.prompt_service,
            agent_invoker=StaticAgentInvoker(
                SharedAgentOutputEnvelope(
                    status="success",
                    summary="Built snapshot-bound narratives.",
                    structured_output={
                        "narratives": [
                            {
                                "control_state_id": "control-state-1",
                                "narrative_type": "control_summary",
                                "content_markdown": "Access review is evidenced by the quarterly review artifact.",
                                "citation_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}],
                            }
                        ]
                    },
                    citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
                )
            ),
            checkpoint_store=checkpoint_store,
            replay_store=replay_store,
            event_emitter=event_emitter,
        )

        self.assertEqual(result.checkpoint.checkpoint_seq, 5)
        self.assertEqual(result.replay_record.bundle_id, "auditflow.writer")
        self.assertEqual(len(result.emitted_outbox_events), 1)
        self.assertEqual(event_emitter.events[0].event_name, "auditflow.package.ready")
        self.assertEqual(checkpoint_store.checkpoints[0].state_after, "exported")
        self.assertEqual(replay_store.records[0].response_schema_ref, "auditflow.writer.output.v1")


if __name__ == "__main__":
    unittest.main()
