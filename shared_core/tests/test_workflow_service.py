from __future__ import annotations

import unittest

from agent_platform import (
    InMemoryCheckpointStore,
    InMemoryOutboxStore,
    InMemoryReplayStore,
    InMemoryWorkflowStateStore,
    PromptAssemblyService,
    PromptAssemblySources,
    SpecialistNodeHandler,
    StaticModelGateway,
    WorkflowExecutionService,
    WorkflowStep,
    build_default_runtime_catalog,
)
from agent_platform.shared import SharedAgentOutputEnvelope


class WorkflowExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_service_runs_persists_resumes_and_dispatches(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
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
        )
        gateway.register_response(
            bundle_id="auditflow.mapper",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Mapped evidence to controls.",
                structured_output={
                    "mapping_candidates": [
                        {
                            "control_id": "control-1",
                            "confidence": 0.9,
                            "ranking_score": 0.95,
                            "rationale": "The evidence clearly shows a completed access review.",
                            "citation_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}],
                        }
                    ]
                },
                citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
            ),
        )

        state_store = InMemoryWorkflowStateStore()
        outbox_store = InMemoryOutboxStore()
        service = WorkflowExecutionService(
            self.prompt_service,
            model_gateway=gateway,
            state_store=state_store,
            checkpoint_store=InMemoryCheckpointStore(),
            replay_store=InMemoryReplayStore(),
            outbox_store=outbox_store,
        )

        normalization_step = WorkflowStep(
            node_name="normalization",
            node_kind="analysis",
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            handler=SpecialistNodeHandler(
                node_name="normalization",
                node_kind="analysis",
                success_events=["auditflow.evidence.normalized"],
                state_patch_builder=lambda context, output: {"current_state": "mapping"},
            ),
        )
        mapping_step = WorkflowStep(
            node_name="mapping",
            node_kind="analysis",
            bundle_id="auditflow.mapper",
            bundle_version="2026-03-16.1",
            handler=SpecialistNodeHandler(
                node_name="mapping",
                node_kind="analysis",
                success_events=["auditflow.mapping.generated"],
                state_patch_builder=lambda context, output: {"current_state": "challenge"},
            ),
        )
        source_builders = {
            "normalization": lambda state: PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                computed={"allowed_evidence_types": ["ticket"]},
            ),
            "mapping": lambda state: PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "evidence_item_id": "evidence-1"},
                retrieval={"evidence_chunk_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}]},
                database={"in_scope_controls": ["control-1"], "framework_name": "SOC2"},
            ),
        }

        first_run = service.run_workflow(
            workflow_run_id="wf-service-1",
            workflow_type="auditflow_cycle",
            initial_state={
                "current_state": "normalization",
                "checkpoint_seq": 0,
                "aggregate_type": "audit_cycle",
                "aggregate_id": "cycle-1",
            },
            steps=[normalization_step],
            source_builders=source_builders,
        )
        self.assertEqual(first_run.final_state["current_state"], "mapping")
        self.assertEqual(service.load_workflow_state("wf-service-1")["current_state"], "mapping")

        resumed = service.resume_workflow(
            workflow_run_id="wf-service-1",
            steps=[normalization_step, mapping_step],
            source_builders=source_builders,
        )
        self.assertEqual(resumed.final_state["current_state"], "challenge")

        seen: list[str] = []
        dispatch_result = service.dispatch_outbox(lambda event: seen.append(event.event_name))
        self.assertEqual(dispatch_result.dispatched_count, 2)
        self.assertEqual(
            seen,
            ["auditflow.evidence.normalized", "auditflow.mapping.generated"],
        )


if __name__ == "__main__":
    unittest.main()
