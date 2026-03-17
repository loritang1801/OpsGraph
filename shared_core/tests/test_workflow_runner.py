from __future__ import annotations

import unittest

from agent_platform import (
    InMemoryCheckpointStore,
    InMemoryEventEmitter,
    InMemoryReplayFixtureStore,
    InMemoryReplayStore,
    ModelGatewayResponse,
    PromptAssemblyService,
    PromptAssemblySources,
    PlannedToolCall,
    ReplayFixture,
    ReplayFixtureLoader,
    ReplayToolFixture,
    SpecialistNodeHandler,
    StaticModelGateway,
    StaticToolAdapter,
    ToolExecutor,
    WorkflowRunner,
    WorkflowStep,
    build_default_runtime_catalog,
)
from agent_platform.shared import SharedAgentOutputEnvelope


class WorkflowRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_runner_executes_steps_with_model_gateway(self) -> None:
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
        runner = WorkflowRunner(self.prompt_service)
        checkpoint_store = InMemoryCheckpointStore()
        replay_store = InMemoryReplayStore()
        event_emitter = InMemoryEventEmitter()

        result = runner.run(
            workflow_run_id="wf-1",
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
                        success_events=["auditflow.package.ready"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "exported",
                            "narrative_ids": ["narrative-1"],
                        },
                    ),
                )
            ],
            source_builders={
                "package_generation": lambda state: PromptAssemblySources(
                    workflow_state={
                        "audit_cycle_id": "cycle-1",
                        "working_snapshot_version": 3,
                    },
                    database={
                        "accepted_mapping_refs": ["mapping-1"],
                        "open_gap_refs": ["gap-1"],
                    },
                    trigger_payload={"export_scope": "cycle_package"},
                )
            },
            model_gateway=gateway,
            event_emitter=event_emitter,
            checkpoint_store=checkpoint_store,
            replay_store=replay_store,
        )

        self.assertEqual(result.final_state["current_state"], "exported")
        self.assertEqual(result.final_state["checkpoint_seq"], 1)
        self.assertEqual(result.step_results[0].checkpoint.checkpoint_seq, 1)
        self.assertEqual(event_emitter.events[0].event_name, "auditflow.package.ready")
        self.assertEqual(replay_store.records[0].bundle_id, "auditflow.writer")

    def test_runner_uses_replay_fixture_loader(self) -> None:
        fixture_store = InMemoryReplayFixtureStore()
        loader = ReplayFixtureLoader(fixture_store)
        fixture_store.save(
            ReplayFixture(
                fixture_key=loader.make_fixture_key(
                    workflow_run_id="wf-replay",
                    checkpoint_seq=1,
                    node_name="communicate",
                ),
                workflow_type="opsgraph_incident",
                node_name="communicate",
                bundle_id="opsgraph.comms",
                bundle_version="2026-03-16.1",
                expected_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Loaded replay draft.",
                    structured_output={
                        "drafts": [
                            {
                                "channel_type": "internal_slack",
                                "fact_set_version": 4,
                                "body_markdown": "Replay draft",
                                "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                            }
                        ]
                    },
                    citations=[{"kind": "incident_fact", "id": "fact-1"}],
                ),
            )
        )
        runner = WorkflowRunner(self.prompt_service)

        result = runner.run(
            workflow_run_id="wf-replay",
            workflow_type="opsgraph_incident",
            initial_state={
                "current_state": "communicate",
                "checkpoint_seq": 0,
                "aggregate_type": "incident",
                "aggregate_id": "incident-1",
            },
            steps=[
                WorkflowStep(
                    node_name="communicate",
                    node_kind="generation",
                    bundle_id="opsgraph.comms",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="communicate",
                        node_kind="generation",
                        success_events=["opsgraph.comms.ready"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "resolve",
                            "publish_ready_draft_ids": ["draft-1"],
                        },
                    ),
                )
            ],
            source_builders={
                "communicate": lambda state: PromptAssemblySources(
                    workflow_state={
                        "incident_id": "incident-1",
                        "current_fact_set_version": 4,
                    },
                    database={"confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}]},
                    trigger_payload={"target_channels": ["internal_slack"]},
                    computed={"channel_policy": {"external_requires_approval": True}},
                )
            },
            replay_loader=loader,
        )

        self.assertEqual(result.final_state["current_state"], "resolve")
        self.assertEqual(result.step_results[0].agent_output.summary, "Loaded replay draft.")

    def test_runner_collects_tool_traces_from_model_gateway(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            response=ModelGatewayResponse(
                agent_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Collected evidence.",
                    structured_output={
                        "normalized_title": "Quarterly Access Review",
                        "evidence_type": "ticket",
                        "summary": "Quarterly user access review completed for production systems.",
                        "captured_at": None,
                        "fresh_until": None,
                        "citation_refs": [{"kind": "artifact", "id": "artifact-1"}],
                    },
                    citations=[{"kind": "artifact", "id": "artifact-1"}],
                ),
                planned_tool_calls=[
                    PlannedToolCall(
                        tool_name="artifact.read",
                        tool_version="2026-03-16.1",
                        arguments={"artifact_id": "artifact-1"},
                    )
                ],
            ),
        )
        executor = ToolExecutor(self.catalog)
        executor.register_adapter(
            "artifact_store",
            StaticToolAdapter(
                {
                    "status": "success",
                    "normalized_payload": {
                        "artifact_id": "artifact-1",
                        "artifact_type": "upload",
                        "parser_status": "completed",
                        "text_ref_ids": ["chunk-1"],
                        "metadata": {},
                    },
                    "provenance": {
                        "adapter_type": "artifact_store",
                        "connection_id": "conn-1",
                        "fetched_at": "2026-03-16T09:00:00Z",
                        "source_locator": "artifact-1",
                    },
                    "raw_ref": {"artifact_id": "artifact-1", "kind": "external_payload"},
                    "warnings": [],
                }
            ),
        )
        runner = WorkflowRunner(self.prompt_service)

        result = runner.run(
            workflow_run_id="wf-tool-1",
            workflow_type="auditflow_cycle",
            initial_state={
                "current_state": "normalization",
                "checkpoint_seq": 0,
                "aggregate_type": "audit_cycle",
                "aggregate_id": "cycle-1",
                "organization_id": "org-1",
                "workspace_id": "ws-1",
                "subject_type": "audit_cycle",
                "subject_id": "cycle-1",
            },
            steps=[
                WorkflowStep(
                    node_name="normalization",
                    node_kind="analysis",
                    bundle_id="auditflow.collector",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="normalization",
                        node_kind="analysis",
                        state_patch_builder=lambda context, output: {"current_state": "mapping"},
                    ),
                )
            ],
            source_builders={
                "normalization": lambda state: PromptAssemblySources(
                    workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                    database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                    computed={"allowed_evidence_types": ["ticket"]},
                )
            },
            model_gateway=gateway,
            tool_executor=executor,
        )
        self.assertEqual(len(result.step_results[0].trace.tool_traces), 1)
        self.assertEqual(result.step_results[0].tool_results[0]["artifact_id"], "artifact-1")

    def test_replay_fixture_loader_rehydrates_tool_results(self) -> None:
        fixture_store = InMemoryReplayFixtureStore()
        loader = ReplayFixtureLoader(fixture_store)
        fixture_store.save(
            ReplayFixture(
                fixture_key=loader.make_fixture_key(
                    workflow_run_id="wf-replay-tools",
                    checkpoint_seq=1,
                    node_name="normalization",
                ),
                workflow_type="auditflow_cycle",
                node_name="normalization",
                bundle_id="auditflow.collector",
                bundle_version="2026-03-16.1",
                expected_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Replay collector result.",
                    structured_output={
                        "normalized_title": "Quarterly Access Review",
                        "evidence_type": "ticket",
                        "summary": "Quarterly user access review completed for production systems.",
                        "captured_at": None,
                        "fresh_until": None,
                        "citation_refs": [{"kind": "artifact", "id": "artifact-1"}],
                    },
                    citations=[{"kind": "artifact", "id": "artifact-1"}],
                ),
                tool_fixtures=[
                    ReplayToolFixture(
                        tool_call_id="tool-call-1",
                        tool_name="artifact.read",
                        tool_version="2026-03-16.1",
                        envelope={
                            "status": "success",
                            "normalized_payload": {
                                "artifact_id": "artifact-1",
                                "artifact_type": "upload",
                                "parser_status": "completed",
                                "text_ref_ids": ["chunk-1"],
                                "metadata": {},
                            },
                            "provenance": {
                                "adapter_type": "artifact_store",
                                "connection_id": "conn-1",
                                "fetched_at": "2026-03-16T09:00:00Z",
                                "source_locator": "artifact-1",
                            },
                            "raw_ref": {"artifact_id": "artifact-1", "kind": "external_payload"},
                            "warnings": [],
                        },
                    )
                ],
            )
        )
        runner = WorkflowRunner(self.prompt_service)
        result = runner.run(
            workflow_run_id="wf-replay-tools",
            workflow_type="auditflow_cycle",
            initial_state={
                "current_state": "normalization",
                "checkpoint_seq": 0,
                "aggregate_type": "audit_cycle",
                "aggregate_id": "cycle-1",
            },
            steps=[
                WorkflowStep(
                    node_name="normalization",
                    node_kind="analysis",
                    bundle_id="auditflow.collector",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="normalization",
                        node_kind="analysis",
                        state_patch_builder=lambda context, output: {"current_state": "mapping"},
                    ),
                )
            ],
            source_builders={
                "normalization": lambda state: PromptAssemblySources(
                    workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                    database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                    computed={"allowed_evidence_types": ["ticket"]},
                )
            },
            replay_loader=loader,
        )
        self.assertEqual(len(result.step_results[0].trace.tool_traces), 1)
        self.assertEqual(result.step_results[0].tool_results[0]["artifact_type"], "upload")


if __name__ == "__main__":
    unittest.main()
