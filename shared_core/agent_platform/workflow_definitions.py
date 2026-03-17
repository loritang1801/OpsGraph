from __future__ import annotations

from typing import Any

from .workflow_registry import WorkflowDefinition, WorkflowRegistry
from .workflow_runner import WorkflowStep
from .node_runtime import SpecialistNodeHandler
from .runtime import PromptAssemblySources


def _with_overrides(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(base)
    if overrides:
        state.update(overrides)
    return state


def _build_auditflow_processing_state(
    workflow_run_id: str,
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    return _with_overrides(
        {
            "organization_id": payload.get("organization_id", "org-1"),
            "workspace_id": payload.get("workspace_id", "ws-1"),
            "subject_type": "audit_cycle",
            "subject_id": payload["audit_cycle_id"],
            "aggregate_type": "audit_cycle",
            "aggregate_id": payload["audit_cycle_id"],
            "current_state": "normalization",
            "checkpoint_seq": 0,
            "audit_cycle_id": payload["audit_cycle_id"],
            "audit_workspace_id": payload.get("audit_workspace_id", "audit-ws-1"),
            "cycle_status": payload.get("cycle_status", "ingesting"),
            "working_snapshot_version": payload.get("working_snapshot_version", 1),
            "source_id": payload["source_id"],
            "source_type": payload.get("source_type", "upload"),
            "artifact_id": payload["artifact_id"],
            "extracted_text_or_summary": payload["extracted_text_or_summary"],
            "allowed_evidence_types": payload.get("allowed_evidence_types", ["document"]),
            "evidence_item_id": payload.get("evidence_item_id", "evidence-1"),
            "evidence_chunk_refs": payload.get("evidence_chunk_refs", []),
            "in_scope_controls": payload.get("in_scope_controls", []),
            "framework_name": payload.get("framework_name", "SOC2"),
            "proposed_mapping_ids": payload.get("proposed_mapping_ids", []),
            "mapping_payloads": payload.get("mapping_payloads", []),
            "freshness_policy": payload.get("freshness_policy", {"mode": "standard"}),
            "control_text": payload.get("control_text", ""),
        },
        overrides,
    )


def _build_auditflow_export_state(
    workflow_run_id: str,
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    return _with_overrides(
        {
            "organization_id": payload.get("organization_id", "org-1"),
            "workspace_id": payload.get("workspace_id", "ws-1"),
            "subject_type": "audit_cycle",
            "subject_id": payload["audit_cycle_id"],
            "aggregate_type": "audit_cycle",
            "aggregate_id": payload["audit_cycle_id"],
            "current_state": "package_generation",
            "checkpoint_seq": 0,
            "audit_cycle_id": payload["audit_cycle_id"],
            "audit_workspace_id": payload.get("audit_workspace_id", "audit-ws-1"),
            "cycle_status": payload.get("cycle_status", "reviewed"),
            "working_snapshot_version": payload["working_snapshot_version"],
            "accepted_mapping_refs": payload.get("accepted_mapping_refs", []),
            "open_gap_refs": payload.get("open_gap_refs", []),
            "export_scope": payload.get("export_scope", "cycle_package"),
        },
        overrides,
    )


def _build_opsgraph_response_state(
    workflow_run_id: str,
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    return _with_overrides(
        {
            "organization_id": payload.get("organization_id", "org-1"),
            "workspace_id": payload.get("workspace_id", "ws-1"),
            "subject_type": "incident",
            "subject_id": payload["incident_id"],
            "aggregate_type": "incident",
            "aggregate_id": payload["incident_id"],
            "current_state": "triage",
            "checkpoint_seq": 0,
            "incident_id": payload["incident_id"],
            "ops_workspace_id": payload.get("ops_workspace_id", "ops-ws-1"),
            "incident_status": payload.get("incident_status", "investigating"),
            "severity": payload.get("severity", "sev2"),
            "signal_ids": payload.get("signal_ids", []),
            "signal_summaries": payload.get("signal_summaries", []),
            "environment_name": payload.get("environment_name", "prod"),
            "current_incident_candidates": payload.get("current_incident_candidates", []),
            "context_bundle_id": payload.get("context_bundle_id", "context-1"),
            "current_fact_set_version": payload.get("current_fact_set_version", 1),
            "context_missing_sources": payload.get("context_missing_sources", []),
            "confirmed_fact_refs": payload.get("confirmed_fact_refs", []),
            "service_id": payload.get("service_id", "service-1"),
            "top_hypothesis_refs": payload.get("top_hypothesis_refs", []),
            "target_channels": payload.get("target_channels", ["internal_slack"]),
            "channel_policy": payload.get("channel_policy", {"external_requires_approval": True}),
        },
        overrides,
    )


def _build_opsgraph_retrospective_state(
    workflow_run_id: str,
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    return _with_overrides(
        {
            "organization_id": payload.get("organization_id", "org-1"),
            "workspace_id": payload.get("workspace_id", "ws-1"),
            "subject_type": "incident",
            "subject_id": payload["incident_id"],
            "aggregate_type": "incident",
            "aggregate_id": payload["incident_id"],
            "current_state": "retrospective",
            "checkpoint_seq": 0,
            "incident_id": payload["incident_id"],
            "ops_workspace_id": payload.get("ops_workspace_id", "ops-ws-1"),
            "incident_status": payload.get("incident_status", "resolved"),
            "severity": payload.get("severity", "sev2"),
            "current_fact_set_version": payload["current_fact_set_version"],
            "confirmed_fact_refs": payload.get("confirmed_fact_refs", []),
            "timeline_refs": payload.get("timeline_refs", []),
            "resolution_summary": payload.get("resolution_summary", ""),
        },
        overrides,
    )


def build_workflow_registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()

    registry.register(
        WorkflowDefinition(
            workflow_name="auditflow_cycle_processing",
            workflow_type="auditflow_cycle",
            description="Normalize evidence, generate mappings, and challenge weak mappings.",
            steps=[
                WorkflowStep(
                    node_name="normalization",
                    node_kind="analysis",
                    bundle_id="auditflow.collector",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="normalization",
                        node_kind="analysis",
                        success_events=["auditflow.evidence.normalized"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "mapping",
                            "parsed_evidence_ids": ["evidence-1"],
                        },
                    ),
                ),
                WorkflowStep(
                    node_name="mapping",
                    node_kind="analysis",
                    bundle_id="auditflow.mapper",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="mapping",
                        node_kind="analysis",
                        success_events=["auditflow.mapping.generated"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "challenge",
                            "proposed_mapping_ids": ["mapping-1"],
                        },
                    ),
                ),
                WorkflowStep(
                    node_name="challenge",
                    node_kind="analysis",
                    bundle_id="auditflow.skeptic",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="challenge",
                        node_kind="analysis",
                        success_events=["auditflow.mapping.flagged"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "human_review",
                            "flagged_mapping_ids": ["mapping-1"],
                        },
                    ),
                ),
            ],
            source_builders={
                "normalization": lambda state: PromptAssemblySources(
                    workflow_state={
                        "audit_cycle_id": state["audit_cycle_id"],
                        "source_id": state["source_id"],
                        "source_type": state["source_type"],
                    },
                    database={
                        "artifact_id": state["artifact_id"],
                        "extracted_text_or_summary": state["extracted_text_or_summary"],
                    },
                    computed={"allowed_evidence_types": state["allowed_evidence_types"]},
                ),
                "mapping": lambda state: PromptAssemblySources(
                    workflow_state={
                        "audit_cycle_id": state["audit_cycle_id"],
                        "evidence_item_id": state["evidence_item_id"],
                    },
                    retrieval={"evidence_chunk_refs": state["evidence_chunk_refs"]},
                    database={
                        "in_scope_controls": state["in_scope_controls"],
                        "framework_name": state["framework_name"],
                    },
                ),
                "challenge": lambda state: PromptAssemblySources(
                    workflow_state={"proposed_mapping_ids": state["proposed_mapping_ids"]},
                    database={
                        "mapping_payloads": state["mapping_payloads"],
                        "control_text": state["control_text"],
                    },
                    computed={"freshness_policy": state["freshness_policy"]},
                ),
            },
            initial_state_builder=_build_auditflow_processing_state,
        )
    )

    registry.register(
        WorkflowDefinition(
            workflow_name="auditflow_export_generation",
            workflow_type="auditflow_cycle",
            description="Generate export-ready narratives for a frozen audit snapshot.",
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
                        "audit_cycle_id": state["audit_cycle_id"],
                        "working_snapshot_version": state["working_snapshot_version"],
                    },
                    database={
                        "accepted_mapping_refs": state["accepted_mapping_refs"],
                        "open_gap_refs": state["open_gap_refs"],
                    },
                    trigger_payload={"export_scope": state["export_scope"]},
                )
            },
            initial_state_builder=_build_auditflow_export_state,
        )
    )

    registry.register(
        WorkflowDefinition(
            workflow_name="opsgraph_incident_response",
            workflow_type="opsgraph_incident",
            description="Triage an incident, generate hypotheses and recommendations, then produce comms drafts.",
            steps=[
                WorkflowStep(
                    node_name="triage",
                    node_kind="analysis",
                    bundle_id="opsgraph.triage",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="triage",
                        node_kind="analysis",
                        success_events=["opsgraph.incident.updated"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "hypothesize",
                            "severity": "sev1",
                        },
                    ),
                ),
                WorkflowStep(
                    node_name="hypothesize",
                    node_kind="analysis",
                    bundle_id="opsgraph.investigator",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="hypothesize",
                        node_kind="analysis",
                        success_events=["opsgraph.hypothesis.generated"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "advise",
                            "top_hypothesis_ids": ["hypothesis-1"],
                        },
                    ),
                ),
                WorkflowStep(
                    node_name="advise",
                    node_kind="analysis",
                    bundle_id="opsgraph.runbook_advisor",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="advise",
                        node_kind="analysis",
                        success_events=["opsgraph.recommendation.generated"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "communicate",
                            "recommendation_ids": ["recommendation-1"],
                        },
                    ),
                ),
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
                ),
            ],
            source_builders={
                "triage": lambda state: PromptAssemblySources(
                    workflow_state={"signal_ids": state["signal_ids"]},
                    database={
                        "signal_summaries": state["signal_summaries"],
                        "current_incident_candidates": state["current_incident_candidates"],
                    },
                    computed={"environment_name": state["environment_name"]},
                ),
                "hypothesize": lambda state: PromptAssemblySources(
                    workflow_state={
                        "incident_id": state["incident_id"],
                        "context_bundle_id": state["context_bundle_id"],
                        "current_fact_set_version": state["current_fact_set_version"],
                        "context_missing_sources": state["context_missing_sources"],
                    },
                    database={"confirmed_fact_refs": state["confirmed_fact_refs"]},
                ),
                "advise": lambda state: PromptAssemblySources(
                    workflow_state={
                        "incident_id": state["incident_id"],
                        "current_fact_set_version": state["current_fact_set_version"],
                        "service_id": state["service_id"],
                    },
                    database={
                        "confirmed_fact_refs": state["confirmed_fact_refs"],
                        "top_hypothesis_refs": state["top_hypothesis_refs"],
                    },
                ),
                "communicate": lambda state: PromptAssemblySources(
                    workflow_state={
                        "incident_id": state["incident_id"],
                        "current_fact_set_version": state["current_fact_set_version"],
                    },
                    database={"confirmed_fact_refs": state["confirmed_fact_refs"]},
                    trigger_payload={"target_channels": state["target_channels"]},
                    computed={"channel_policy": state["channel_policy"]},
                ),
            },
            initial_state_builder=_build_opsgraph_response_state,
        )
    )

    registry.register(
        WorkflowDefinition(
            workflow_name="opsgraph_retrospective",
            workflow_type="opsgraph_incident",
            description="Generate a postmortem from confirmed facts and timeline state.",
            steps=[
                WorkflowStep(
                    node_name="retrospective",
                    node_kind="generation",
                    bundle_id="opsgraph.postmortem_reviewer",
                    bundle_version="2026-03-16.1",
                    handler=SpecialistNodeHandler(
                        node_name="retrospective",
                        node_kind="generation",
                        success_events=["opsgraph.postmortem.ready"],
                        state_patch_builder=lambda context, output: {
                            "current_state": "retrospective_completed",
                            "postmortem_id": "postmortem-1",
                        },
                    ),
                )
            ],
            source_builders={
                "retrospective": lambda state: PromptAssemblySources(
                    workflow_state={
                        "incident_id": state["incident_id"],
                        "current_fact_set_version": state["current_fact_set_version"],
                    },
                    database={
                        "confirmed_fact_refs": state["confirmed_fact_refs"],
                        "timeline_refs": state["timeline_refs"],
                        "resolution_summary": state["resolution_summary"],
                    },
                )
            },
            initial_state_builder=_build_opsgraph_retrospective_state,
        )
    )

    return registry
