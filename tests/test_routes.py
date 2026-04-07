from __future__ import annotations

from datetime import UTC, datetime
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.shared_runtime import load_shared_agent_platform
from opsgraph_app.replay_worker_monitor_page import render_replay_worker_monitor_html
from opsgraph_app.routes import (
    _event_topics,
    _event_topic,
    _format_sse_message,
    _matches_event_topic,
    _normalize_resume_after_id,
    _replay_worker_status_event_id,
    _resolve_outbox_event_context,
    map_domain_error,
    paginate_collection,
    success_envelope,
)

_AP = load_shared_agent_platform()


class OpsGraphReplayWorkerMonitorPageTests(unittest.TestCase):
    def test_monitor_page_renderer_includes_admin_endpoints(self) -> None:
        markup = render_replay_worker_monitor_html()

        self.assertIn("OpsGraph Replay Worker Monitor", markup)
        self.assertIn("Remote Provider Smoke", markup)
        self.assertIn("Remote Smoke Drilldown", markup)
        self.assertIn("smokeCopyFormat", markup)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke", markup)
        self.assertIn("/api/v1/opsgraph/runtime-capabilities", markup)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke-summary", markup)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke-runs", markup)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-presets", markup)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-default-preset", markup)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-shift-schedule", markup)


class OpsGraphRouteErrorMappingTests(unittest.TestCase):
    def test_maps_incident_not_found_key_error_to_404(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(KeyError("incident-404"), path="/api/v1/opsgraph/incidents/incident-404"),
            status_code=404,
            error_code="INCIDENT_NOT_FOUND",
        )

    def test_maps_conflict_value_error_to_409(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(ValueError("APPROVAL_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/comms"),
            status_code=409,
            error_code="APPROVAL_REQUIRED",
        )

    def test_maps_fact_version_conflict_to_409(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("FACT_VERSION_CONFLICT"),
                path="/api/v1/opsgraph/incidents/incident-1/facts",
            ),
            status_code=409,
            error_code="FACT_VERSION_CONFLICT",
        )

    def test_maps_validation_value_error_to_422(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(ValueError("ROOT_CAUSE_FACT_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/resolve"),
            status_code=422,
            error_code="ROOT_CAUSE_FACT_REQUIRED",
        )

    def test_maps_replay_not_executed_to_409(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("REPLAY_RUN_NOT_EXECUTED"),
                path="/api/v1/opsgraph/replays/replay-1/evaluate",
            ),
            status_code=409,
            error_code="REPLAY_RUN_NOT_EXECUTED",
        )

    def test_maps_replay_status_conflict_to_409(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("REPLAY_STATUS_CONFLICT"),
                path="/api/v1/opsgraph/replays/replay-1/status",
            ),
            status_code=409,
            error_code="REPLAY_STATUS_CONFLICT",
        )

    def test_maps_replay_evaluation_unavailable_to_503(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("REPLAY_EVALUATION_UNAVAILABLE"),
                path="/api/v1/opsgraph/replays/replay-1/evaluate",
            ),
            status_code=503,
            error_code="REPLAY_EVALUATION_UNAVAILABLE",
        )

    def test_maps_replay_worker_alert_policy_validation_to_400(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("INVALID_REPLAY_WORKER_ALERT_CRITICAL_THRESHOLD"),
                path="/api/v1/opsgraph/replays/worker-alert-policy",
            ),
            status_code=400,
            error_code="INVALID_REPLAY_WORKER_ALERT_CRITICAL_THRESHOLD",
        )

    def test_maps_replay_worker_monitor_preset_validation_to_400(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("INVALID_REPLAY_MONITOR_PRESET_COPY_FORMAT"),
                path="/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            ),
            status_code=400,
            error_code="INVALID_REPLAY_MONITOR_PRESET_COPY_FORMAT",
        )

    def test_maps_approval_task_not_found_to_404(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                KeyError("approval-task-404"),
                path="/api/v1/opsgraph/approvals/approval-task-404/decision",
            ),
            status_code=404,
            error_code="APPROVAL_TASK_NOT_FOUND",
        )

    def test_maps_approval_input_errors_to_422(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("APPROVAL_PUBLISH_FACT_SET_REQUIRED"),
                path="/api/v1/opsgraph/approvals/approval-task-1/decision",
            ),
            status_code=422,
            error_code="APPROVAL_PUBLISH_FACT_SET_REQUIRED",
        )

    def test_maps_idempotency_conflict_to_409(self) -> None:
        _AP.assert_domain_error_mapping(
            self,
            map_domain_error(
                ValueError("IDEMPOTENCY_CONFLICT"),
                path="/api/v1/opsgraph/alerts/prometheus",
            ),
            status_code=409,
            error_code="IDEMPOTENCY_CONFLICT",
        )

    def test_success_envelope_includes_cursor_and_request_metadata(self) -> None:
        payload = success_envelope(
            [{"id": "incident-1"}],
            request_id="req-ops-1",
            next_cursor="cursor-1",
            has_more=True,
        )

        _AP.assert_success_envelope(
            self,
            payload,
            data_expected_fields={"0.id": "incident-1"},
            meta_expected_fields={
                "request_id": "req-ops-1",
                "next_cursor": "cursor-1",
                "has_more": True,
            },
        )

    def test_paginate_collection_uses_cursor_metadata(self) -> None:
        next_cursor = _AP.assert_paginated_window(
            self,
            paginate_collection([1, 2, 3], limit=2),
            expected_items=[1, 2],
            has_more=True,
            next_cursor_present=True,
        )
        _AP.assert_paginated_window(
            self,
            paginate_collection(
                [1, 2, 3],
                cursor=next_cursor,
                limit=2,
            ),
            expected_items=[3],
            has_more=False,
            next_cursor_present=False,
        )

    def test_invalid_pagination_cursor_raises_contract_code(self) -> None:
        with self.assertRaisesRegex(ValueError, "INVALID_CURSOR"):
            paginate_collection([1, 2], cursor="bad-cursor", limit=1)

    def test_resolves_sse_event_context_from_runtime_state(self) -> None:
        state_store = SimpleNamespace(
            load=lambda workflow_run_id: SimpleNamespace(
                state={
                    "organization_id": "org-1",
                    "ops_workspace_id": "ops-ws-1",
                    "subject_type": "incident",
                    "subject_id": "incident-1",
                }
            )
        )
        service = SimpleNamespace(runtime_stores=SimpleNamespace(state_store=state_store))
        event = SimpleNamespace(
            event_id="evt-1",
            event_name="workflow.step.completed",
            workflow_run_id="wf-1",
            aggregate_type="incident",
            aggregate_id="incident-1",
            payload={"current_state": "mitigate"},
            emitted_at=datetime(2026, 3, 17, 10, 0, tzinfo=UTC),
        )

        context = _resolve_outbox_event_context(service, event)

        _AP.assert_fields(
            self,
            context,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "subject_type": "incident",
                "subject_id": "incident-1",
                "topic": "workflow",
            },
        )

    def test_resolves_sse_event_context_from_event_payload_fallback(self) -> None:
        state_store = SimpleNamespace(load=lambda workflow_run_id: (_ for _ in ()).throw(KeyError(workflow_run_id)))
        service = SimpleNamespace(runtime_stores=SimpleNamespace(state_store=state_store))
        event = SimpleNamespace(
            event_id="evt-2",
            event_name="opsgraph.incident.updated",
            workflow_run_id="wf-missing",
            aggregate_type="incident",
            aggregate_id="incident-2",
            payload={
                "organization_id": "org-2",
                "workspace_id": "ops-ws-2",
                "incident_id": "incident-2",
            },
            emitted_at=datetime(2026, 3, 17, 10, 5, tzinfo=UTC),
        )

        context = _resolve_outbox_event_context(service, event)

        _AP.assert_fields(
            self,
            context,
            expected_fields={
                "organization_id": "org-2",
                "workspace_id": "ops-ws-2",
                "subject_type": "incident",
                "subject_id": "incident-2",
            },
        )

    def test_formats_sse_message_with_json_payload(self) -> None:
        message = _format_sse_message(
            event_id="evt-1",
            event_name="opsgraph.comms.ready",
            payload={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_sse_message_contract(
            self,
            message,
            event_id="evt-1",
            event_name="opsgraph.comms.ready",
            expected_substrings=['"workspace_id": "ops-ws-1"'],
            resolved_topic=_event_topic("opsgraph.comms.ready"),
            expected_topic="opsgraph",
        )

    def test_replay_worker_status_event_id_uses_latest_history_timestamp(self) -> None:
        event_id = _replay_worker_status_event_id(
            {
                "workspace_id": "ops-ws-1",
                "current": {
                    "last_seen_at": "2026-03-27T09:30:02Z",
                },
                "history": [
                    {"emitted_at": "2026-03-27T09:30:03Z"},
                    {"emitted_at": "2026-03-27T09:30:02Z"},
                ],
            }
        )

        self.assertEqual(event_id, "replay-worker:ops-ws-1:2026-03-27T09:30:03Z")

    def test_event_topics_include_domain_specific_aliases(self) -> None:
        context = {
            "topic": "opsgraph",
            "workspace_id": "ops-ws-1",
            "subject_type": "incident",
            "subject_id": "incident-1",
            "payload": {"incident_id": "incident-1"},
        }

        topics = _event_topics(context)

        _AP.assert_event_topic_routing(
            self,
            context,
            topics,
            expected_topics=[
                "opsgraph.workspace.ops-ws-1",
                "opsgraph.incident.incident-1",
            ],
            matcher=_matches_event_topic,
            matching_topic="opsgraph.incident.incident-1",
            rejected_topic="workflow",
        )

    def test_missing_last_event_id_does_not_block_stream_progress(self) -> None:
        pending = [SimpleNamespace(event=SimpleNamespace(event_id="evt-1"))]

        _AP.assert_resume_after_id_contract(
            self,
            _normalize_resume_after_id,
            pending,
            missing_id="evt-missing",
            existing_id="evt-1",
        )


if __name__ == "__main__":
    unittest.main()
