from __future__ import annotations

import unittest
from datetime import UTC, datetime

from agent_platform import (
    InMemoryOutboxStore,
    InMemoryWorkflowStateStore,
    OutboxDispatcher,
    OutboxStoreEmitter,
    PromptAssemblyService,
    PromptAssemblySources,
    StaticModelGateway,
    WorkflowRunner,
    WorkflowStep,
    SpecialistNodeHandler,
    build_default_runtime_catalog,
)
from agent_platform.shared import SharedAgentOutputEnvelope


class PersistenceAndDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_runner_persists_state_after_step(self) -> None:
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
        state_store = InMemoryWorkflowStateStore()
        runner = WorkflowRunner(self.prompt_service)

        result = runner.run(
            workflow_run_id="wf-persist-1",
            workflow_type="auditflow_cycle",
            initial_state={
                "current_state": "package_generation",
                "checkpoint_seq": 0,
                "aggregate_type": "audit_cycle",
                "aggregate_id": "cycle-1",
            },
            steps=[
                WorkflowStep(
                    node_name="package_generation",
                    node_kind="generation",
                    bundle_id="auditflow.writer",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="package_generation",
                        node_kind="generation",
                        state_patch_builder=lambda context, output: {"current_state": "exported"},
                    ),
                )
            ],
            source_builders={
                "package_generation": lambda state: PromptAssemblySources(
                    workflow_state={"audit_cycle_id": "cycle-1", "working_snapshot_version": 3},
                    database={"accepted_mapping_refs": ["mapping-1"], "open_gap_refs": ["gap-1"]},
                    trigger_payload={"export_scope": "cycle_package"},
                )
            },
            model_gateway=gateway,
            state_store=state_store,
        )

        stored = state_store.load("wf-persist-1")
        self.assertEqual(stored.checkpoint_seq, 1)
        self.assertEqual(stored.state["current_state"], "exported")
        self.assertEqual(result.final_state["current_state"], "exported")

    def test_outbox_dispatcher_marks_events_dispatched(self) -> None:
        outbox = InMemoryOutboxStore()
        emitter = OutboxStoreEmitter(outbox)
        seen: list[str] = []

        emitter.emit(
            {
                "event_id": "evt-1",
                "event_name": "auditflow.package.ready",
                "workflow_run_id": "wf-1",
                "workflow_type": "auditflow_cycle",
                "node_name": "package_generation",
                "aggregate_type": "audit_cycle",
                "aggregate_id": "cycle-1",
                "payload": {"current_state": "exported"},
                "emitted_at": datetime.now(UTC),
            }
        )
        dispatcher = OutboxDispatcher(outbox, lambda event: seen.append(event.event_name))
        dispatch_result = dispatcher.dispatch_pending(dispatched_at=datetime.now(UTC))

        self.assertEqual(dispatch_result.attempted_count, 1)
        self.assertEqual(dispatch_result.dispatched_count, 1)
        self.assertEqual(seen, ["auditflow.package.ready"])
        self.assertEqual(outbox.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
