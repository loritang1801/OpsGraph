from __future__ import annotations

import unittest

from agent_platform import (
    InMemoryCheckpointStore,
    InMemoryOutboxStore,
    InMemoryReplayFixtureStore,
    InMemoryReplayStore,
    InMemoryWorkflowStateStore,
    PromptAssemblyService,
    ReplayFixture,
    ReplayFixtureLoader,
    ReplayWorkflowRequest,
    StartWorkflowRequest,
    StaticModelGateway,
    WorkflowApiService,
    WorkflowExecutionService,
    build_default_runtime_catalog,
    build_workflow_registry,
)
from agent_platform.shared import SharedAgentOutputEnvelope


class WorkflowApiServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)
        self.registry = build_workflow_registry()

    def test_api_service_lists_and_starts_workflow(self) -> None:
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
        execution_service = WorkflowExecutionService(
            self.prompt_service,
            model_gateway=gateway,
            state_store=InMemoryWorkflowStateStore(),
            checkpoint_store=InMemoryCheckpointStore(),
            replay_store=InMemoryReplayStore(),
            outbox_store=InMemoryOutboxStore(),
        )
        api_service = WorkflowApiService(self.registry, execution_service)

        listed = api_service.list_workflows()
        started = api_service.start_workflow(
            StartWorkflowRequest(
                workflow_name="auditflow_export_generation",
                workflow_run_id="wf-api-1",
                input_payload={
                    "audit_cycle_id": "cycle-1",
                    "working_snapshot_version": 3,
                    "accepted_mapping_refs": ["mapping-1"],
                    "open_gap_refs": [],
                },
            )
        )

        self.assertEqual(len(listed), 4)
        self.assertEqual(started.current_state, "exported")
        self.assertEqual(started.workflow_type, "auditflow_cycle")

    def test_api_service_accepts_dict_requests(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="auditflow.writer",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Generated package narratives from dict request.",
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
        execution_service = WorkflowExecutionService(
            self.prompt_service,
            model_gateway=gateway,
            state_store=InMemoryWorkflowStateStore(),
            checkpoint_store=InMemoryCheckpointStore(),
            replay_store=InMemoryReplayStore(),
            outbox_store=InMemoryOutboxStore(),
        )
        api_service = WorkflowApiService(self.registry, execution_service)

        started = api_service.start_workflow(
            {
                "workflow_name": "auditflow_export_generation",
                "workflow_run_id": "wf-api-dict-1",
                "input_payload": {
                    "audit_cycle_id": "cycle-1",
                    "working_snapshot_version": 3,
                    "accepted_mapping_refs": ["mapping-1"],
                    "open_gap_refs": [],
                },
                "state_overrides": {},
            }
        )

        self.assertEqual(started.current_state, "exported")

    def test_api_service_replays_workflow(self) -> None:
        fixture_store = InMemoryReplayFixtureStore()
        loader = ReplayFixtureLoader(fixture_store)
        fixture_store.save(
            ReplayFixture(
                fixture_key=loader.make_fixture_key(
                    workflow_run_id="wf-api-replay-1",
                    checkpoint_seq=1,
                    node_name="package_generation",
                ),
                workflow_type="auditflow_cycle",
                node_name="package_generation",
                bundle_id="auditflow.writer",
                bundle_version="2026-03-16.1",
                expected_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Replay package narratives.",
                    structured_output={
                        "narratives": [
                            {
                                "control_state_id": "control-state-1",
                                "narrative_type": "control_summary",
                                "content_markdown": "Replay grounded narrative.",
                                "citation_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}],
                            }
                        ]
                    },
                    citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
                ),
            )
        )
        execution_service = WorkflowExecutionService(
            self.prompt_service,
            replay_loader=loader,
            state_store=InMemoryWorkflowStateStore(),
            checkpoint_store=InMemoryCheckpointStore(),
            replay_store=InMemoryReplayStore(),
            outbox_store=InMemoryOutboxStore(),
        )
        api_service = WorkflowApiService(self.registry, execution_service)

        replayed = api_service.replay_workflow(
            ReplayWorkflowRequest(
                workflow_name="auditflow_export_generation",
                workflow_run_id="wf-api-replay-1",
                input_payload={
                    "audit_cycle_id": "cycle-1",
                    "working_snapshot_version": 3,
                    "accepted_mapping_refs": ["mapping-1"],
                    "open_gap_refs": [],
                },
            )
        )
        self.assertEqual(replayed.current_state, "exported")


if __name__ == "__main__":
    unittest.main()
