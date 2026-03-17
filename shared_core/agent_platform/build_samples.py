from __future__ import annotations

from .model_gateway import ModelGatewayResponse, PlannedToolCall, StaticModelGateway
from .shared import SharedAgentOutputEnvelope
from .tool_executor import StaticToolAdapter, ToolExecutor


def register_auditflow_demo_gateway_responses(model_gateway: StaticModelGateway) -> None:
    model_gateway.register_response(
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
    model_gateway.register_response(
        bundle_id="auditflow.mapper",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Mapped evidence to in-scope controls.",
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
    model_gateway.register_response(
        bundle_id="auditflow.skeptic",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Challenged weak mappings.",
            structured_output={
                "mapping_flags": [
                    {
                        "mapping_id": "mapping-1",
                        "issue_type": "needs_reviewer_confirmation",
                        "severity": "medium",
                        "recommended_action": "Review mapping before export.",
                    }
                ],
                "gaps": [],
            },
            citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
        ),
    )
    model_gateway.register_response(
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
                        "content_markdown": "Quarterly access review evidence supports the control.",
                        "citation_refs": [{"kind": "evidence_chunk", "id": "chunk-1"}],
                    }
                ]
            },
            citations=[{"kind": "evidence_chunk", "id": "chunk-1"}],
        ),
    )


def register_opsgraph_demo_gateway_responses(model_gateway: StaticModelGateway) -> None:
    model_gateway.register_response(
        bundle_id="opsgraph.triage",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Triaged the incident.",
            structured_output={
                "dedupe_group_key": "checkout-api:high-error-rate",
                "severity": "sev1",
                "severity_confidence": 0.88,
                "title": "Elevated 5xx on checkout-api",
                "service_id": "service-1",
                "blast_radius_summary": "Checkout traffic is impacted across the primary region.",
            },
        ),
    )
    model_gateway.register_response(
        bundle_id="opsgraph.investigator",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Generated incident hypotheses.",
            structured_output={
                "hypotheses": [
                    {
                        "title": "Recent deploy introduced connection pool exhaustion.",
                        "confidence": 0.82,
                        "rank": 1,
                        "evidence_refs": [{"kind": "deployment", "id": "deploy-123"}],
                        "verification_steps": [
                            {
                                "step_order": 1,
                                "instruction_text": "Check DB connection saturation metrics.",
                            }
                        ],
                    }
                ]
            },
            citations=[{"kind": "deployment", "id": "deploy-123"}],
        ),
    )
    model_gateway.register_response(
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
                        "title": "Roll back deployment 123",
                        "instructions_markdown": "Rollback checkout-api deploy 123.",
                        "evidence_refs": [{"kind": "deployment", "id": "deploy-123"}],
                    }
                ]
            },
            citations=[{"kind": "deployment", "id": "deploy-123"}],
        ),
    )
    model_gateway.register_response(
        bundle_id="opsgraph.comms",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Generated incident communication drafts.",
            structured_output={
                "drafts": [
                    {
                        "channel_type": "internal_slack",
                        "fact_set_version": 1,
                        "body_markdown": "We are investigating elevated error rates affecting checkout.",
                        "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                    }
                ]
            },
            citations=[{"kind": "incident_fact", "id": "fact-1"}],
        ),
    )
    model_gateway.register_response(
        bundle_id="opsgraph.postmortem_reviewer",
        bundle_version="2026-03-16.1",
        response=SharedAgentOutputEnvelope(
            status="success",
            summary="Generated incident postmortem.",
            structured_output={
                "postmortem_markdown": "At 09:00 UTC, checkout-api began returning elevated 5xx responses.",
                "follow_up_actions": [
                    {"title": "Add connection pool saturation alert", "owner_hint": "payments-sre"}
                ],
                "replay_capture_hints": ["include deployment 123 metadata"],
            },
            citations=[{"kind": "incident_fact", "id": "fact-1"}],
        ),
    )


def register_auditflow_demo_tool_adapters(tool_executor: ToolExecutor) -> None:
    tool_executor.register_adapter(
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
    tool_executor.register_adapter(
        "chunk_store",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "artifact_id": "artifact-1",
                    "chunk_id": "chunk-1",
                    "text": "Quarterly access review completed.",
                    "locator": {"page": 1},
                },
                "provenance": {
                    "adapter_type": "chunk_store",
                    "connection_id": "conn-1",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "artifact-1/chunk-1",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "vector_store",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "items": [
                        {
                            "evidence_chunk_id": "chunk-1",
                            "evidence_item_id": "evidence-1",
                            "score": 0.95,
                            "summary": "Quarterly access review completed.",
                        }
                    ]
                },
                "provenance": {
                    "adapter_type": "vector_store",
                    "connection_id": "conn-1",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "vector-search",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "control_catalog",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "controls": [
                        {
                            "control_id": "control-1",
                            "title": "Access Review",
                            "objective_text": "Review user access quarterly.",
                        }
                    ]
                },
                "provenance": {
                    "adapter_type": "control_catalog",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "SOC2/control-1",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "auditflow_database",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {"candidates": []},
                "provenance": {
                    "adapter_type": "auditflow_database",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "auditflow-db",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "snapshot_reader",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "accepted_mapping_ids": ["mapping-1"],
                    "open_gap_ids": [],
                    "prior_narrative_ids": [],
                },
                "provenance": {
                    "adapter_type": "snapshot_reader",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "snapshot-3",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "snapshot_validator",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "eligible": True,
                    "blocker_codes": [],
                    "current_snapshot_version": 3,
                },
                "provenance": {
                    "adapter_type": "snapshot_validator",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "snapshot-validator",
                },
                "warnings": [],
            }
        ),
    )


def register_opsgraph_demo_tool_adapters(tool_executor: ToolExecutor) -> None:
    tool_executor.register_adapter(
        "opsgraph_database",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "signals": [
                        {
                            "signal_id": "signal-1",
                            "source": "grafana",
                            "correlation_key": "checkout-api:high-error-rate",
                            "summary": "Elevated 5xx errors on checkout-api.",
                            "observed_at": "2026-03-16T09:00:00Z",
                        }
                    ]
                },
                "provenance": {
                    "adapter_type": "opsgraph_database",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "opsgraph-db",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "context_bundle_reader",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "context_bundle_id": "context-1",
                    "summary": "Recent deploy 123 preceded elevated 5xx rates.",
                    "missing_sources": [],
                    "refs": [{"kind": "deployment", "id": "deploy-123"}],
                },
                "provenance": {
                    "adapter_type": "context_bundle_reader",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "context-1",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "github",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "deployments": [
                        {
                            "deployment_id": "deploy-123",
                            "commit_ref": "abc123",
                            "actor": "release-bot",
                            "deployed_at": "2026-03-16T08:55:00Z",
                        }
                    ]
                },
                "provenance": {
                    "adapter_type": "github",
                    "connection_id": "conn-gh-1",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "github/deployments/123",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "service_registry",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "services": [
                        {
                            "service_id": "service-1",
                            "name": "checkout-api",
                            "owner_team": "payments-sre",
                            "dependency_names": ["postgres", "redis"],
                            "runbook_refs": ["runbook-1"],
                        }
                    ]
                },
                "provenance": {
                    "adapter_type": "service_registry",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "service-registry/checkout-api",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "channel_policy",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {
                    "preview_body": "We are investigating elevated error rates affecting checkout.",
                    "max_length": 2000,
                    "policy_warnings": [],
                },
                "provenance": {
                    "adapter_type": "channel_policy",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "slack/internal_slack",
                },
                "warnings": [],
            }
        ),
    )
    tool_executor.register_adapter(
        "approval_store",
        StaticToolAdapter(
            {
                "status": "success",
                "normalized_payload": {"approvals": []},
                "provenance": {
                    "adapter_type": "approval_store",
                    "fetched_at": "2026-03-16T09:00:00Z",
                    "source_locator": "approval-store",
                },
                "warnings": [],
            }
        ),
    )
