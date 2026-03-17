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

from opsgraph_app.routes import (
    _event_topics,
    _event_topic,
    _format_sse_message,
    _matches_event_topic,
    _normalize_resume_after_id,
    _resolve_outbox_event_context,
    map_domain_error,
    paginate_collection,
    success_envelope,
)


class OpsGraphRouteErrorMappingTests(unittest.TestCase):
    def test_maps_incident_not_found_key_error_to_404(self) -> None:
        status_code, payload = map_domain_error(KeyError("incident-404"), path="/api/v1/opsgraph/incidents/incident-404")

        self.assertEqual(status_code, 404)
        self.assertEqual(payload["error"]["code"], "INCIDENT_NOT_FOUND")

    def test_maps_conflict_value_error_to_409(self) -> None:
        status_code, payload = map_domain_error(ValueError("APPROVAL_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/comms")

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "APPROVAL_REQUIRED")

    def test_maps_fact_version_conflict_to_409(self) -> None:
        status_code, payload = map_domain_error(
            ValueError("FACT_VERSION_CONFLICT"),
            path="/api/v1/opsgraph/incidents/incident-1/facts",
        )

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "FACT_VERSION_CONFLICT")

    def test_maps_validation_value_error_to_422(self) -> None:
        status_code, payload = map_domain_error(ValueError("ROOT_CAUSE_FACT_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/resolve")

        self.assertEqual(status_code, 422)
        self.assertEqual(payload["error"]["code"], "ROOT_CAUSE_FACT_REQUIRED")

    def test_maps_replay_not_executed_to_409(self) -> None:
        status_code, payload = map_domain_error(
            ValueError("REPLAY_RUN_NOT_EXECUTED"),
            path="/api/v1/opsgraph/replays/replay-1/evaluate",
        )

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "REPLAY_RUN_NOT_EXECUTED")

    def test_maps_replay_status_conflict_to_409(self) -> None:
        status_code, payload = map_domain_error(
            ValueError("REPLAY_STATUS_CONFLICT"),
            path="/api/v1/opsgraph/replays/replay-1/status",
        )

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "REPLAY_STATUS_CONFLICT")

    def test_maps_replay_evaluation_unavailable_to_503(self) -> None:
        status_code, payload = map_domain_error(
            ValueError("REPLAY_EVALUATION_UNAVAILABLE"),
            path="/api/v1/opsgraph/replays/replay-1/evaluate",
        )

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["error"]["code"], "REPLAY_EVALUATION_UNAVAILABLE")

    def test_maps_idempotency_conflict_to_409(self) -> None:
        status_code, payload = map_domain_error(
            ValueError("IDEMPOTENCY_CONFLICT"),
            path="/api/v1/opsgraph/alerts/prometheus",
        )

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_success_envelope_includes_cursor_and_request_metadata(self) -> None:
        payload = success_envelope(
            [{"id": "incident-1"}],
            request_id="req-ops-1",
            next_cursor="cursor-1",
            has_more=True,
        )

        self.assertEqual(payload["data"][0]["id"], "incident-1")
        self.assertEqual(payload["meta"]["request_id"], "req-ops-1")
        self.assertEqual(payload["meta"]["next_cursor"], "cursor-1")
        self.assertTrue(payload["meta"]["has_more"])

    def test_paginate_collection_uses_cursor_metadata(self) -> None:
        page_one, next_cursor, has_more = paginate_collection([1, 2, 3], limit=2)
        page_two, second_cursor, second_has_more = paginate_collection(
            [1, 2, 3],
            cursor=next_cursor,
            limit=2,
        )

        self.assertEqual(page_one, [1, 2])
        self.assertTrue(has_more)
        self.assertIsNotNone(next_cursor)
        self.assertEqual(page_two, [3])
        self.assertFalse(second_has_more)
        self.assertIsNone(second_cursor)

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

        self.assertEqual(context["workspace_id"], "ops-ws-1")
        self.assertEqual(context["subject_type"], "incident")
        self.assertEqual(context["subject_id"], "incident-1")
        self.assertEqual(context["topic"], "workflow")

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

        self.assertEqual(context["organization_id"], "org-2")
        self.assertEqual(context["workspace_id"], "ops-ws-2")
        self.assertEqual(context["subject_type"], "incident")
        self.assertEqual(context["subject_id"], "incident-2")

    def test_formats_sse_message_with_json_payload(self) -> None:
        message = _format_sse_message(
            event_id="evt-1",
            event_name="opsgraph.comms.ready",
            payload={"workspace_id": "ops-ws-1"},
        )

        self.assertIn("id: evt-1", message)
        self.assertIn("event: opsgraph.comms.ready", message)
        self.assertIn('"workspace_id": "ops-ws-1"', message)
        self.assertEqual(_event_topic("opsgraph.comms.ready"), "opsgraph")

    def test_event_topics_include_domain_specific_aliases(self) -> None:
        context = {
            "topic": "opsgraph",
            "workspace_id": "ops-ws-1",
            "subject_type": "incident",
            "subject_id": "incident-1",
            "payload": {"incident_id": "incident-1"},
        }

        topics = _event_topics(context)

        self.assertIn("opsgraph.workspace.ops-ws-1", topics)
        self.assertIn("opsgraph.incident.incident-1", topics)
        self.assertTrue(_matches_event_topic(context, "opsgraph.incident.incident-1"))
        self.assertFalse(_matches_event_topic(context, "workflow"))

    def test_missing_last_event_id_does_not_block_stream_progress(self) -> None:
        pending = [SimpleNamespace(event=SimpleNamespace(event_id="evt-1"))]

        self.assertIsNone(_normalize_resume_after_id(pending, "evt-missing"))
        self.assertEqual(_normalize_resume_after_id(pending, "evt-1"), "evt-1")


if __name__ == "__main__":
    unittest.main()
