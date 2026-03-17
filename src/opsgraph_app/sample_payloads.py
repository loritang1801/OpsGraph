def incident_response_payload() -> dict:
    return {
        "incident_id": "incident-1",
        "ops_workspace_id": "ops-ws-1",
        "signal_ids": ["signal-1"],
        "signal_summaries": [
            {
                "signal_id": "signal-1",
                "source": "grafana",
                "correlation_key": "checkout-api:high-error-rate",
                "summary": "Elevated 5xx errors on checkout-api.",
                "observed_at": "2026-03-16T09:00:00Z",
            }
        ],
        "current_incident_candidates": [],
        "context_bundle_id": "context-1",
        "current_fact_set_version": 1,
        "confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
        "top_hypothesis_refs": [{"kind": "deployment", "id": "deploy-123"}],
        "target_channels": ["internal_slack"],
    }


def replay_case_payload(*, replay_case_id: str) -> dict:
    payload = incident_response_payload()
    payload["incident_id"] = f"replay-case:{replay_case_id}"
    payload["ops_workspace_id"] = "ops-ws-1"
    payload["signal_ids"] = [f"signal-replay-{replay_case_id}"]
    payload["signal_summaries"] = [
        {
            "signal_id": f"signal-replay-{replay_case_id}",
            "source": "replay_case",
            "correlation_key": f"replay-case:{replay_case_id}",
            "summary": f"Replay case {replay_case_id} seeded incident response inputs.",
            "observed_at": "2026-03-16T09:00:00Z",
        }
    ]
    payload["current_incident_candidates"] = []
    payload["context_bundle_id"] = f"replay-case-context-{replay_case_id}"
    return payload


def retrospective_payload() -> dict:
    return {
        "incident_id": "incident-1",
        "ops_workspace_id": "ops-ws-1",
        "current_fact_set_version": 1,
        "confirmed_fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
        "timeline_refs": [{"kind": "timeline_event", "id": "timeline-1"}],
        "resolution_summary": "Rollback restored checkout availability.",
    }


def incident_response_request(
    *,
    workflow_run_id: str = "opsgraph-demo-incident",
    state_overrides: dict | None = None,
) -> dict:
    return {
        "workflow_name": "opsgraph_incident_response",
        "workflow_run_id": workflow_run_id,
        "input_payload": incident_response_payload(),
        "state_overrides": state_overrides or {},
    }


def retrospective_request(
    *,
    workflow_run_id: str = "opsgraph-demo-retrospective",
    state_overrides: dict | None = None,
) -> dict:
    return {
        "workflow_name": "opsgraph_retrospective",
        "workflow_run_id": workflow_run_id,
        "input_payload": retrospective_payload(),
        "state_overrides": state_overrides or {},
    }


def incident_response_command(
    *,
    workflow_run_id: str = "opsgraph-demo-incident",
    state_overrides: dict | None = None,
) -> dict:
    payload = incident_response_payload()
    payload["workflow_run_id"] = workflow_run_id
    payload["state_overrides"] = state_overrides or {}
    return payload


def retrospective_command(
    *,
    workflow_run_id: str = "opsgraph-demo-retrospective",
    state_overrides: dict | None = None,
) -> dict:
    payload = retrospective_payload()
    payload["workflow_run_id"] = workflow_run_id
    payload["state_overrides"] = state_overrides or {}
    return payload


def alert_ingest_command(
    *,
    correlation_key: str = "checkout-api:high-error-rate",
    summary: str = "Elevated 5xx errors on checkout-api.",
    source: str = "prometheus",
    observed_at: str = "2026-03-16T09:00:00Z",
) -> dict:
    return {
        "ops_workspace_id": "ops-ws-1",
        "correlation_key": correlation_key,
        "summary": summary,
        "source": source,
        "observed_at": observed_at,
        "organization_id": "org-1",
        "workspace_id": "ws-1",
    }


def fact_create_command(
    *,
    fact_type: str = "impact",
    statement: str = "Checkout requests are failing for 27% of users.",
) -> dict:
    return {
        "fact_type": fact_type,
        "statement": statement,
        "source_refs": [{"kind": "signal", "id": "signal-1"}],
        "expected_fact_set_version": 1,
    }


def fact_retract_command(*, reason: str = "Metric query was corrected.") -> dict:
    return {
        "reason": reason,
        "expected_fact_set_version": 2,
    }


def hypothesis_decision_command(*, decision: str = "accept", comment: str = "Matches deployment timing.") -> dict:
    return {
        "decision": decision,
        "comment": comment,
    }


def severity_override_command(*, severity: str = "sev2", reason: str = "Impact confirmed lower than initial signal.") -> dict:
    return {
        "severity": severity,
        "reason": reason,
    }


def comms_publish_command(
    *,
    expected_fact_set_version: int = 1,
    approval_task_id: str | None = None,
) -> dict:
    return {
        "expected_fact_set_version": expected_fact_set_version,
        "approval_task_id": approval_task_id,
    }


def recommendation_decision_command(
    *,
    decision: str = "approve",
    approval_task_id: str | None = "approval-task-1",
    comment: str = "Approved by incident commander.",
) -> dict:
    return {
        "decision": decision,
        "comment": comment,
        "approval_task_id": approval_task_id,
    }


def resolve_incident_command(
    *,
    resolution_summary: str = "Rolled back deployment 123 and error rate returned to baseline.",
) -> dict:
    return {
        "resolution_summary": resolution_summary,
        "root_cause_fact_ids": ["fact-1"],
    }


def close_incident_command(
    *,
    close_reason: str = "Postmortem draft created and no further action pending.",
) -> dict:
    return {
        "close_reason": close_reason,
    }


def replay_run_command(*, incident_id: str = "incident-1", model_bundle_version: str = "opsgraph-v1.2") -> dict:
    return {
        "incident_id": incident_id,
        "replay_case_id": None,
        "model_bundle_version": model_bundle_version,
    }


def replay_case_run_command(*, replay_case_id: str = "replay-case-1", model_bundle_version: str = "opsgraph-v1.2") -> dict:
    return {
        "incident_id": None,
        "replay_case_id": replay_case_id,
        "model_bundle_version": model_bundle_version,
    }


def replay_baseline_capture_command(
    *,
    incident_id: str = "incident-1",
    model_bundle_version: str = "opsgraph-v1.2",
    workflow_run_id: str | None = None,
) -> dict:
    return {
        "incident_id": incident_id,
        "model_bundle_version": model_bundle_version,
        "workflow_run_id": workflow_run_id,
    }


def replay_status_command(*, status: str = "completed") -> dict:
    return {
        "status": status,
    }


def replay_evaluation_command(*, baseline_id: str) -> dict:
    return {
        "baseline_id": baseline_id,
    }
