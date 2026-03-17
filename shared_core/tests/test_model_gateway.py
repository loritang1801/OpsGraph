from __future__ import annotations

import unittest

from agent_platform import (
    GatewayAgentInvoker,
    NodeExecutionContext,
    ModelGatewayResponse,
    PlannedToolCall,
    PromptAssemblyService,
    PromptAssemblySources,
    StaticModelGateway,
    StaticToolAdapter,
    ToolExecutor,
    build_default_runtime_catalog,
)
from agent_platform.errors import NodeExecutionError
from agent_platform.shared import SharedAgentOutputEnvelope


class ModelGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.prompt_service = PromptAssemblyService(self.catalog)

    def test_gateway_invoker_returns_registered_response(self) -> None:
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
        assembled = self.prompt_service.assemble(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                computed={"allowed_evidence_types": ["ticket"]},
            ),
        )

        output = GatewayAgentInvoker(gateway).invoke(
            assembled_prompt=assembled,
            context=NodeExecutionContext(
                node_name="collector",
                node_kind="analysis",
                workflow_run_id="wf-1",
                workflow_type="auditflow_cycle",
                subject_type="audit_cycle",
                subject_id="cycle-1",
                current_state="normalization",
                bundle_id="auditflow.collector",
                bundle_version="2026-03-16.1",
                prompt_sources=PromptAssemblySources(),
            ),
        )
        self.assertEqual(output.agent_output.summary, "Collected evidence.")

    def test_gateway_raises_when_response_is_missing(self) -> None:
        assembled = self.prompt_service.assemble(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                computed={"allowed_evidence_types": ["ticket"]},
            ),
        )

        with self.assertRaises(NodeExecutionError):
            GatewayAgentInvoker(StaticModelGateway()).invoke(
                assembled_prompt=assembled,
                context=NodeExecutionContext(
                    node_name="collector",
                    node_kind="analysis",
                    workflow_run_id="wf-2",
                    workflow_type="auditflow_cycle",
                    subject_type="audit_cycle",
                    subject_id="cycle-1",
                    current_state="normalization",
                    bundle_id="auditflow.collector",
                    bundle_version="2026-03-16.1",
                    prompt_sources=PromptAssemblySources(),
                ),
            )

    def test_gateway_invoker_executes_planned_tool_calls(self) -> None:
        gateway = StaticModelGateway()
        gateway.register_response(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            response=ModelGatewayResponse(
                agent_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Collected evidence with tool support.",
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
        assembled = self.prompt_service.assemble(
            bundle_id="auditflow.collector",
            bundle_version="2026-03-16.1",
            sources=PromptAssemblySources(
                workflow_state={"audit_cycle_id": "cycle-1", "source_id": "source-1", "source_type": "upload"},
                database={"artifact_id": "artifact-1", "extracted_text_or_summary": "sample"},
                computed={"allowed_evidence_types": ["ticket"]},
            ),
        )

        result = GatewayAgentInvoker(gateway, tool_executor=executor).invoke(
            assembled_prompt=assembled,
            context=NodeExecutionContext(
                node_name="collector",
                node_kind="analysis",
                workflow_run_id="wf-3",
                workflow_type="auditflow_cycle",
                organization_id="org-1",
                workspace_id="ws-1",
                subject_type="audit_cycle",
                subject_id="cycle-1",
                current_state="normalization",
                bundle_id="auditflow.collector",
                bundle_version="2026-03-16.1",
                prompt_sources=PromptAssemblySources(),
            ),
        )
        self.assertEqual(len(result.tool_traces), 1)
        self.assertEqual(result.tool_results[0].normalized_payload["artifact_id"], "artifact-1")


if __name__ == "__main__":
    unittest.main()
