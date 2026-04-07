from __future__ import annotations

import hashlib
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


def _stable_workflow_entity_id(prefix: str, workflow_run_id: str, *parts: object) -> str:
    normalized = "||".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(f"{workflow_run_id}::{normalized}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _auditflow_control_lookup(context) -> dict[str, dict[str, Any]]:
    controls = context.prompt_sources.database.get("in_scope_controls", [])
    lookup: dict[str, dict[str, Any]] = {}
    for control in controls:
        if isinstance(control, str):
            normalized = {
                "control_state_id": control,
                "control_code": control,
            }
        elif isinstance(control, dict):
            normalized = dict(control)
        else:
            continue
        for key in ("control_state_id", "control_id", "control_code"):
            value = normalized.get(key)
            if value:
                lookup[str(value)] = normalized
    return lookup


def _build_auditflow_normalization_patch(context, output) -> dict[str, Any]:
    evidence_item_id = str(
        context.prompt_sources.workflow_state.get("evidence_item_id")
        or context.subject_id
        or "evidence-1"
    )
    return {
        "current_state": "mapping",
        "parsed_evidence_ids": [evidence_item_id],
    }


def _build_auditflow_mapping_patch(context, output) -> dict[str, Any]:
    structured_output = (
        dict(output.structured_output)
        if isinstance(output.structured_output, dict)
        else {}
    )
    control_lookup = _auditflow_control_lookup(context)
    evidence_item_id = str(context.prompt_sources.workflow_state.get("evidence_item_id") or "evidence-1")
    mapping_payloads: list[dict[str, Any]] = []
    for index, candidate in enumerate(structured_output.get("mapping_candidates", [])):
        if not isinstance(candidate, dict):
            continue
        control_reference = (
            candidate.get("control_state_id")
            or candidate.get("control_code")
            or candidate.get("control_id")
        )
        resolved_control = (
            control_lookup.get(str(control_reference))
            if control_reference is not None
            else None
        ) or {}
        control_state_id = str(
            resolved_control.get("control_state_id")
            or control_reference
            or f"control-{index + 1}"
        )
        control_code = str(
            resolved_control.get("control_code")
            or candidate.get("control_code")
            or control_state_id
        )
        mapping_id = _stable_workflow_entity_id(
            "mapping",
            context.workflow_run_id,
            context.subject_id,
            evidence_item_id,
            control_state_id,
            index,
        )
        mapping_payloads.append(
            {
                "mapping_id": mapping_id,
                "control_state_id": control_state_id,
                "control_code": control_code,
                "confidence": candidate.get("confidence"),
                "ranking_score": candidate.get("ranking_score"),
                "rationale_summary": candidate.get("rationale"),
                "citation_refs": (
                    [dict(item) for item in candidate.get("citation_refs", []) if isinstance(item, dict)]
                    if isinstance(candidate.get("citation_refs"), list)
                    else []
                ),
            }
        )
    return {
        "current_state": "challenge",
        "proposed_mapping_ids": [
            str(payload["mapping_id"])
            for payload in mapping_payloads
            if payload.get("mapping_id") is not None
        ],
        "mapping_payloads": mapping_payloads,
    }


def _build_auditflow_challenge_patch(context, output) -> dict[str, Any]:
    structured_output = (
        dict(output.structured_output)
        if isinstance(output.structured_output, dict)
        else {}
    )
    flagged_mapping_ids = [
        str(item["mapping_id"])
        for item in structured_output.get("mapping_flags", [])
        if isinstance(item, dict) and item.get("mapping_id") is not None
    ]
    return {
        "current_state": "human_review",
        "flagged_mapping_ids": flagged_mapping_ids,
    }


def _build_auditflow_export_patch(context, output) -> dict[str, Any]:
    structured_output = (
        dict(output.structured_output)
        if isinstance(output.structured_output, dict)
        else {}
    )
    narrative_ids: list[str] = []
    snapshot_version = context.prompt_sources.workflow_state.get("working_snapshot_version")
    for index, narrative in enumerate(structured_output.get("narratives", [])):
        if not isinstance(narrative, dict):
            continue
        narrative_ids.append(
            _stable_workflow_entity_id(
                "narrative",
                context.workflow_run_id,
                context.subject_id,
                snapshot_version,
                narrative.get("control_state_id"),
                narrative.get("narrative_type"),
                index,
            )
        )
    return {
        "current_state": "exported",
        "narrative_ids": narrative_ids,
    }


def _build_auditflow_processing_state(
    workflow_run_id: str,
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    return _with_overrides(
        {
            "organization_id": payload.get("organization_id", "org-1"),
            "workspace_id": payload.get("workspace_id", payload.get("audit_workspace_id", "ws-1")),
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
            "mapping_memory_context": payload.get("mapping_memory_context", []),
            "challenge_memory_context": payload.get("challenge_memory_context", []),
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
            "workspace_id": payload.get("workspace_id", payload.get("audit_workspace_id", "ws-1")),
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
            "investigation_memory_context": payload.get("investigation_memory_context", []),
            "recommendation_memory_context": payload.get("recommendation_memory_context", []),
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
            "postmortem_memory_context": payload.get("postmortem_memory_context", []),
        },
        overrides,
    )


def _opsgraph_structured_output(output) -> dict[str, Any]:
    return dict(output.structured_output) if isinstance(output.structured_output, dict) else {}


def _opsgraph_ref_list(
    refs: object,
    *,
    fallback_kind: str,
    fallback_id: str,
) -> list[dict[str, Any]]:
    normalized = [
        {
            "kind": str(item.get("kind") or fallback_kind),
            "id": str(item.get("id") or fallback_id),
        }
        for item in (refs or [])
        if isinstance(item, dict) and (item.get("id") or fallback_id)
    ]
    return normalized or [{"kind": fallback_kind, "id": fallback_id}]


def _opsgraph_signal_service_id(signal_summaries: object) -> str | None:
    for signal in signal_summaries or []:
        if not isinstance(signal, dict):
            continue
        correlation_key = str(signal.get("correlation_key") or "")
        if ":" in correlation_key:
            candidate = correlation_key.split(":", 1)[0].strip()
            if candidate:
                return candidate
    return None


def _build_opsgraph_triage_patch(context, output) -> dict[str, Any]:
    structured_output = _opsgraph_structured_output(output)
    signal_summaries = context.prompt_sources.database.get("signal_summaries", [])
    first_signal = signal_summaries[0] if signal_summaries and isinstance(signal_summaries[0], dict) else {}
    dedupe_group_key = str(
        structured_output.get("dedupe_group_key")
        or first_signal.get("correlation_key")
        or context.subject_id
        or "incident"
    )
    service_id = str(
        structured_output.get("service_id")
        or _opsgraph_signal_service_id(signal_summaries)
        or "service-1"
    )
    title = str(
        structured_output.get("title")
        or first_signal.get("summary")
        or f"Incident impacting {service_id}"
    )
    return {
        "current_state": "hypothesize",
        "severity": str(structured_output.get("severity") or "sev2"),
        "severity_confidence": structured_output.get("severity_confidence"),
        "service_id": service_id,
        "title": title,
        "dedupe_group_key": dedupe_group_key,
        "blast_radius_summary": str(
            structured_output.get("blast_radius_summary") or ""
        ),
    }


def _build_opsgraph_hypothesis_patch(context, output) -> dict[str, Any]:
    structured_output = _opsgraph_structured_output(output)
    incident_id = str(context.prompt_sources.workflow_state.get("incident_id") or context.subject_id or "incident")
    hypothesis_payloads: list[dict[str, Any]] = []
    for index, hypothesis in enumerate(structured_output.get("hypotheses", [])):
        if not isinstance(hypothesis, dict):
            continue
        rank = int(hypothesis.get("rank") or index + 1)
        hypothesis_id = _stable_workflow_entity_id(
            "hypothesis",
            context.workflow_run_id,
            incident_id,
            rank,
            hypothesis.get("title"),
            index,
        )
        verification_steps = [
            {
                "step_order": int(step.get("step_order") or step_index + 1),
                "instruction_text": str(step.get("instruction_text") or ""),
            }
            for step_index, step in enumerate(hypothesis.get("verification_steps", []))
            if isinstance(step, dict)
        ]
        hypothesis_payloads.append(
            {
                "hypothesis_id": hypothesis_id,
                "title": str(hypothesis.get("title") or f"Hypothesis {index + 1}"),
                "confidence": hypothesis.get("confidence"),
                "rank": rank,
                "evidence_refs": _opsgraph_ref_list(
                    hypothesis.get("evidence_refs"),
                    fallback_kind="incident_fact",
                    fallback_id="fact-unknown",
                ),
                "verification_steps": verification_steps,
            }
        )
    ordered_payloads = sorted(
        hypothesis_payloads,
        key=lambda item: (
            int(item.get("rank") or 0),
            str(item.get("hypothesis_id") or ""),
        ),
    )
    top_hypothesis_ids = [
        str(item["hypothesis_id"])
        for item in ordered_payloads[:3]
        if item.get("hypothesis_id") is not None
    ]
    return {
        "current_state": "advise",
        "hypothesis_ids": [
            str(item["hypothesis_id"])
            for item in ordered_payloads
            if item.get("hypothesis_id") is not None
        ],
        "top_hypothesis_ids": top_hypothesis_ids,
        "top_hypothesis_refs": [
            {"kind": "hypothesis", "id": hypothesis_id}
            for hypothesis_id in top_hypothesis_ids
        ],
        "hypothesis_payloads": ordered_payloads,
    }


def _build_opsgraph_recommendation_patch(context, output) -> dict[str, Any]:
    structured_output = _opsgraph_structured_output(output)
    incident_id = str(context.prompt_sources.workflow_state.get("incident_id") or context.subject_id or "incident")
    recommendation_payloads: list[dict[str, Any]] = []
    approval_task_payloads: list[dict[str, Any]] = []
    for index, recommendation in enumerate(structured_output.get("recommendations", [])):
        if not isinstance(recommendation, dict):
            continue
        recommendation_id = _stable_workflow_entity_id(
            "recommendation",
            context.workflow_run_id,
            incident_id,
            recommendation.get("title"),
            index,
        )
        requires_approval = bool(recommendation.get("requires_approval"))
        approval_task_id = (
            _stable_workflow_entity_id(
                "approval-task",
                context.workflow_run_id,
                incident_id,
                recommendation_id,
            )
            if requires_approval
            else None
        )
        recommendation_payloads.append(
            {
                "recommendation_id": recommendation_id,
                "recommendation_type": str(recommendation.get("recommendation_type") or "mitigate"),
                "risk_level": str(recommendation.get("risk_level") or "medium"),
                "requires_approval": requires_approval,
                "title": str(recommendation.get("title") or f"Recommendation {index + 1}"),
                "instructions_markdown": str(recommendation.get("instructions_markdown") or ""),
                "evidence_refs": _opsgraph_ref_list(
                    recommendation.get("evidence_refs"),
                    fallback_kind="incident_fact",
                    fallback_id="fact-unknown",
                ),
                "approval_task_id": approval_task_id,
            }
        )
        if approval_task_id is not None:
            approval_task_payloads.append(
                {
                    "approval_task_id": approval_task_id,
                    "recommendation_id": recommendation_id,
                    "status": "pending",
                }
            )
    return {
        "current_state": "communicate",
        "recommendation_ids": [
            str(item["recommendation_id"])
            for item in recommendation_payloads
            if item.get("recommendation_id") is not None
        ],
        "pending_approval_task_ids": [
            str(item["approval_task_id"])
            for item in approval_task_payloads
            if item.get("approval_task_id") is not None
        ],
        "recommendation_payloads": recommendation_payloads,
        "approval_task_payloads": approval_task_payloads,
    }


def _build_opsgraph_comms_patch(context, output) -> dict[str, Any]:
    structured_output = _opsgraph_structured_output(output)
    incident_id = str(context.prompt_sources.workflow_state.get("incident_id") or context.subject_id or "incident")
    draft_payloads: list[dict[str, Any]] = []
    for index, draft in enumerate(structured_output.get("drafts", [])):
        if not isinstance(draft, dict):
            continue
        channel_type = str(draft.get("channel_type") or "internal_slack")
        fact_set_version = int(draft.get("fact_set_version") or 0)
        draft_id = _stable_workflow_entity_id(
            "draft",
            context.workflow_run_id,
            incident_id,
            channel_type,
            fact_set_version,
            index,
        )
        draft_payloads.append(
            {
                "draft_id": draft_id,
                "channel_type": channel_type,
                "fact_set_version": fact_set_version,
                "body_markdown": str(draft.get("body_markdown") or ""),
                "fact_refs": _opsgraph_ref_list(
                    draft.get("fact_refs"),
                    fallback_kind="incident_fact",
                    fallback_id="fact-unknown",
                ),
            }
        )
    draft_ids = [
        str(item["draft_id"])
        for item in draft_payloads
        if item.get("draft_id") is not None
    ]
    return {
        "current_state": "resolve",
        "comms_draft_ids": draft_ids,
        "publish_ready_draft_ids": draft_ids,
        "comms_payloads": draft_payloads,
    }


def _build_opsgraph_postmortem_patch(context, output) -> dict[str, Any]:
    structured_output = _opsgraph_structured_output(output)
    incident_id = str(context.prompt_sources.workflow_state.get("incident_id") or context.subject_id or "incident")
    postmortem_id = _stable_workflow_entity_id(
        "postmortem",
        context.workflow_run_id,
        incident_id,
        context.prompt_sources.workflow_state.get("current_fact_set_version"),
    )
    return {
        "current_state": "retrospective_completed",
        "postmortem_id": postmortem_id,
        "postmortem_markdown": str(structured_output.get("postmortem_markdown") or ""),
        "follow_up_actions": [
            dict(item)
            for item in structured_output.get("follow_up_actions", [])
            if isinstance(item, dict)
        ],
        "replay_capture_hints": [
            str(item)
            for item in structured_output.get("replay_capture_hints", [])
            if item
        ],
    }


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
                        state_patch_builder=_build_auditflow_normalization_patch,
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
                        state_patch_builder=_build_auditflow_mapping_patch,
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
                        state_patch_builder=_build_auditflow_challenge_patch,
                    ),
                ),
            ],
            source_builders={
                "normalization": lambda state: PromptAssemblySources(
                    workflow_state={
                        "audit_cycle_id": state["audit_cycle_id"],
                        "source_id": state["source_id"],
                        "source_type": state["source_type"],
                        "evidence_item_id": state["evidence_item_id"],
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
                    memory={"accepted_pattern_memories": state.get("mapping_memory_context", [])},
                    database={
                        "in_scope_controls": state["in_scope_controls"],
                        "framework_name": state["framework_name"],
                    },
                ),
                "challenge": lambda state: PromptAssemblySources(
                    workflow_state={"proposed_mapping_ids": state["proposed_mapping_ids"]},
                    memory={"challenge_pattern_memories": state.get("challenge_memory_context", [])},
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
                        state_patch_builder=_build_auditflow_export_patch,
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
                        state_patch_builder=_build_opsgraph_triage_patch,
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
                        state_patch_builder=_build_opsgraph_hypothesis_patch,
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
                        state_patch_builder=_build_opsgraph_recommendation_patch,
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
                        state_patch_builder=_build_opsgraph_comms_patch,
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
                    memory={"memory_context": state.get("investigation_memory_context", [])},
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
                    memory={"memory_context": state.get("recommendation_memory_context", [])},
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
                        state_patch_builder=_build_opsgraph_postmortem_patch,
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
                    memory={"memory_context": state.get("postmortem_memory_context", [])},
                )
            },
            initial_state_builder=_build_opsgraph_retrospective_state,
        )
    )

    return registry
