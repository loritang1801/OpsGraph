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
    build_workflow_registry,
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
                memory={"accepted_pattern_memories": []},
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

    def test_service_builds_dynamic_opsgraph_state_from_structured_outputs(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="opsgraph.triage",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Triaged the incident.",
                structured_output={
                    "dedupe_group_key": "payments-api:latency-spike",
                    "severity": "sev1",
                    "severity_confidence": 0.91,
                    "title": "Elevated latency on payments-api",
                    "service_id": "payments-api",
                    "blast_radius_summary": "Payments traffic is degraded.",
                },
            ),
        )
        gateway.register_response(
            bundle_id="opsgraph.investigator",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Generated incident hypotheses.",
                structured_output={
                    "hypotheses": [
                        {
                            "title": "Recent dependency change increased request latency.",
                            "confidence": 0.82,
                            "rank": 1,
                            "evidence_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                            "verification_steps": [
                                {"step_order": 1, "instruction_text": "Check the dependency metrics."}
                            ],
                        }
                    ]
                },
                citations=[{"kind": "incident_fact", "id": "fact-1"}],
            ),
        )
        gateway.register_response(
            bundle_id="opsgraph.runbook_advisor",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Recommended mitigation steps.",
                structured_output={
                    "recommendations": [
                        {
                            "recommendation_type": "mitigate",
                            "risk_level": "high_risk",
                            "requires_approval": True,
                            "title": "Rollback the latest payments-api change",
                            "instructions_markdown": "Rollback the latest release and confirm latency recovers.",
                            "evidence_refs": [{"kind": "hypothesis", "id": "hypothesis-1"}],
                        }
                    ]
                },
                citations=[{"kind": "hypothesis", "id": "hypothesis-1"}],
            ),
        )
        gateway.register_response(
            bundle_id="opsgraph.comms",
            bundle_version="2026-03-16.1",
            response=SharedAgentOutputEnvelope(
                status="success",
                summary="Generated incident communication drafts.",
                structured_output={
                    "drafts": [
                        {
                            "channel_type": "internal_slack",
                            "fact_set_version": 2,
                            "body_markdown": "Investigating elevated latency on payments-api.",
                            "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                        }
                    ]
                },
                citations=[{"kind": "incident_fact", "id": "fact-1"}],
            ),
        )

        registry = build_workflow_registry()
        definition = registry.get("opsgraph_incident_response")
        state_store = InMemoryWorkflowStateStore()
        service = WorkflowExecutionService(
            self.prompt_service,
            model_gateway=gateway,
            state_store=state_store,
            checkpoint_store=InMemoryCheckpointStore(),
            replay_store=InMemoryReplayStore(),
            outbox_store=InMemoryOutboxStore(),
        )

        initial_state = definition.initial_state_builder(
            "wf-opsgraph-1",
            {
                "incident_id": "incident-1",
                "ops_workspace_id": "ops-ws-1",
                "signal_ids": ["signal-1"],
                "signal_summaries": [
                    {
                        "signal_id": "signal-1",
                        "source": "grafana",
                        "correlation_key": "payments-api:latency-spike",
                        "summary": "Latency spike on payments-api.",
                        "observed_at": "2026-03-16T09:00:00Z",
                    }
                ],
                "current_incident_candidates": [],
                "context_bundle_id": "context-1",
                "current_fact_set_version": 2,
                "service_id": "payments-api",
                "confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                "top_hypothesis_refs": [{"kind": "deployment", "id": "deploy-123"}],
                "target_channels": ["internal_slack"],
                "organization_id": "org-1",
                "workspace_id": "ws-1",
            },
            None,
        )
        result = service.run_workflow(
            workflow_run_id="wf-opsgraph-1",
            workflow_type=definition.workflow_type,
            initial_state=initial_state,
            steps=definition.steps,
            source_builders=definition.source_builders,
        )

        self.assertEqual(result.final_state["current_state"], "resolve")
        self.assertEqual(result.final_state["service_id"], "payments-api")
        self.assertEqual(result.final_state["severity"], "sev1")
        self.assertEqual(len(result.final_state["top_hypothesis_ids"]), 1)
        self.assertEqual(len(result.final_state["recommendation_ids"]), 1)
        self.assertEqual(len(result.final_state["publish_ready_draft_ids"]), 1)
        persisted = service.load_workflow_state("wf-opsgraph-1")
        self.assertEqual(persisted["service_id"], "payments-api")
        self.assertEqual(persisted["title"], "Elevated latency on payments-api")


if __name__ == "__main__":
    unittest.main()
