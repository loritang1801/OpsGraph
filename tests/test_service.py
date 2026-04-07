from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.repository import (
    ApprovalTaskRow,
    ArtifactBlobRow,
    CommsDraftRow,
    ContextBundleRow,
    MemoryRecordRow,
    PostmortemRow,
    ReplayCaseRow,
    ReplayRunRow,
)
from opsgraph_app.sample_payloads import (
    approval_decision_command,
    alert_ingest_command,
    close_incident_command,
    comms_publish_command,
    fact_create_command,
    fact_retract_command,
    hypothesis_decision_command,
    incident_response_command,
    postmortem_finalize_command,
    replay_baseline_capture_command,
    replay_evaluation_command,
    replay_case_run_command,
    recommendation_decision_command,
    replay_status_command,
    replay_run_command,
    resolve_incident_command,
    retrospective_command,
    severity_override_command,
)
from opsgraph_app.worker import OpsGraphReplayWorker
from shared_core.agent_platform.sqlalchemy_stores import ReplayRecordRow, WorkflowStateRow
from tests._repo_temp import cleanup_repo_tempdir, create_repo_tempdir


class OpsGraphServiceTests(unittest.TestCase):
    @staticmethod
    def _issue_auth_context(service, *, email: str, required_role: str):
        issue = service.auth_service.create_session(
            {
                "email": email,
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        return service.auth_service.build_authorizer().authorize(
            required_role=required_role,
            authorization=f"Bearer {issue.response.access_token}",
            organization_id="org-1",
        )

    def test_query_incident_workspace_and_alert_ingest(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        incidents = service.list_incidents("ops-ws-1")
        workspace = service.get_incident_workspace("incident-1")
        ingest = service.ingest_alert(alert_ingest_command())
        workspace_after = service.get_incident_workspace("incident-1")

        self.assertEqual(len(incidents), 1)
        self.assertEqual(workspace.incident.incident_key, "INC-2026-0001")
        self.assertEqual(len(workspace.signals), 1)
        self.assertEqual(workspace.signals[0].signal_id, "signal-1")
        self.assertEqual(ingest.incident_id, "incident-1")
        self.assertFalse(ingest.incident_created)
        self.assertEqual(len(workspace_after.signals), 2)
        self.assertTrue(any(item.signal_id == ingest.signal_id for item in workspace_after.signals))
        self.assertEqual(ingest.accepted_signals, 1)
        self.assertIsNotNone(ingest.workflow_run_id)

    def test_alert_ingest_emits_signal_and_incident_outbox_events(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        ingest = service.ingest_alert(alert_ingest_command())
        pending = service.runtime_stores.outbox_store.list_pending()
        matching_events = [
            item.event
            for item in pending
            if item.event.workflow_run_id == ingest.workflow_run_id
        ]
        event_names = [event.event_name for event in matching_events]

        self.assertIn("opsgraph.signal.ingested", event_names)
        self.assertIn("opsgraph.incident.updated", event_names)

    def test_alert_ingest_is_idempotent_for_repeated_key(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.ingest_alert(
            alert_ingest_command(
                correlation_key="checkout-api:idempotent-alert",
                summary="Idempotent alert",
            ),
            idempotency_key="ops-alert-1",
        )
        second = service.ingest_alert(
            alert_ingest_command(
                correlation_key="checkout-api:idempotent-alert",
                summary="Idempotent alert",
            ),
            idempotency_key="ops-alert-1",
        )
        workspace = service.get_incident_workspace(first.incident_id)

        matching = [item for item in workspace.signals if item.dedupe_key == "checkout-api:idempotent-alert"]
        self.assertEqual(first.signal_id, second.signal_id)
        self.assertEqual(first.workflow_run_id, second.workflow_run_id)
        self.assertEqual(len(matching), 1)

    def test_alert_ingest_rejects_idempotency_conflict(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.ingest_alert(
            alert_ingest_command(
                correlation_key="checkout-api:idempotency-conflict",
                summary="First idempotent alert",
            ),
            idempotency_key="ops-alert-conflict",
        )

        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            service.ingest_alert(
                alert_ingest_command(
                    correlation_key="checkout-api:idempotency-conflict",
                    summary="Different idempotent alert payload",
                    source="grafana",
                ),
                idempotency_key="ops-alert-conflict",
            )

    def test_alert_ingest_groups_similar_service_alerts_within_time_window(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-spike",
                summary="Payments API latency spike on checkout path",
                source="grafana",
                observed_at="2026-03-16T10:00:00Z",
            )
        )
        second = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-burst",
                summary="Payments API latency burst on checkout path",
                source="grafana",
                observed_at="2026-03-16T10:20:00Z",
            )
        )

        self.assertTrue(first.incident_created)
        self.assertFalse(second.incident_created)
        self.assertEqual(first.incident_id, second.incident_id)

    def test_alert_ingest_creates_new_incident_outside_aggregation_window(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-spike",
                summary="Payments API latency spike on checkout path",
                source="grafana",
                observed_at="2026-03-16T10:00:00Z",
            )
        )
        second = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-burst",
                summary="Payments API latency burst on checkout path",
                source="grafana",
                observed_at="2026-03-16T13:30:00Z",
            )
        )

        self.assertTrue(first.incident_created)
        self.assertTrue(second.incident_created)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_incident_workspace_contract_fields_serialize_with_aliases(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        workspace = service.get_incident_workspace("incident-1")
        workspace_payload = workspace.model_dump(by_alias=True)
        incident_payload = workspace.incident.model_dump(by_alias=True)
        signal_payload = workspace.signals[0].model_dump(by_alias=True)

        self.assertEqual(incident_payload["id"], "incident-1")
        self.assertEqual(incident_payload["status"], "investigating")
        self.assertEqual(incident_payload["service_id"], "checkout-api")
        self.assertIsNotNone(incident_payload["acknowledged_at"])
        self.assertEqual(signal_payload["id"], "signal-1")
        self.assertIn("facts", workspace_payload)
        self.assertEqual(workspace_payload["facts"][0]["id"], "fact-1")

    def test_list_incidents_supports_status_severity_and_service_filters(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        created = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-spike",
                summary="Payments API latency spike",
            )
        )
        service.override_severity("incident-1", severity_override_command(severity="sev1"))
        service.resolve_incident("incident-1", resolve_incident_command())

        resolved = service.list_incidents("ops-ws-1", status="resolved")
        investigating = service.list_incidents("ops-ws-1", status="investigating")
        sev1 = service.list_incidents("ops-ws-1", severity="sev1")
        payments = service.list_incidents("ops-ws-1", service_id="payments-api")

        self.assertEqual([item.incident_id for item in resolved], ["incident-1"])
        self.assertEqual([item.incident_id for item in investigating], [created.incident_id])
        self.assertEqual([item.incident_id for item in sev1], ["incident-1"])
        self.assertEqual([item.incident_id for item in payments], [created.incident_id])

    def test_add_fact_uses_contract_conflict_code_for_stale_fact_set(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "FACT_VERSION_CONFLICT"):
            service.add_fact(
                "incident-1",
                fact_create_command() | {"expected_fact_set_version": 2},
            )

    def test_add_fact_is_idempotent_for_repeated_key(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.add_fact(
            "incident-1",
            fact_create_command(),
            idempotency_key="fact-add-1",
        )
        second = service.add_fact(
            "incident-1",
            fact_create_command(),
            idempotency_key="fact-add-1",
        )

        self.assertEqual(first.fact_id, second.fact_id)
        self.assertEqual(first.current_fact_set_version, second.current_fact_set_version)

    def test_manual_actions_record_audit_logs_and_timeline_actor_context(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "operator@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        auth_context = service.auth_service.build_authorizer().authorize(
            required_role="operator",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id="org-1",
        )

        created = service.add_fact(
            "incident-1",
            fact_create_command(),
            idempotency_key="fact-audit-1",
            auth_context=auth_context,
            request_id="req-audit-1",
        )
        logs = service.list_audit_logs(
            "incident-1",
            action_type="incident.add_fact",
            actor_user_id="user-operator-1",
        )
        workspace = service.get_incident_workspace("incident-1")
        matching_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "incident_fact" and item.subject_id == created.fact_id
        ]

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].actor_type, "user")
        self.assertEqual(logs[0].actor_user_id, "user-operator-1")
        self.assertEqual(logs[0].actor_role, "operator")
        self.assertIsNotNone(logs[0].session_id)
        self.assertEqual(logs[0].request_id, "req-audit-1")
        self.assertEqual(logs[0].idempotency_key, "fact-audit-1")
        self.assertEqual(logs[0].subject_type, "incident_fact")
        self.assertEqual(logs[0].subject_id, created.fact_id)
        self.assertEqual(logs[0].result_payload["fact_id"], created.fact_id)
        self.assertEqual(logs[0].request_payload["fact_type"], "impact")
        self.assertEqual(len(matching_timeline), 1)
        self.assertEqual(matching_timeline[0].actor_type, "user")
        self.assertEqual(matching_timeline[0].actor_id, "user-operator-1")
        self.assertEqual(matching_timeline[0].payload["fact_set_version"], created.current_fact_set_version)

    def test_idempotent_manual_action_does_not_duplicate_audit_log(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "operator@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        auth_context = service.auth_service.build_authorizer().authorize(
            required_role="operator",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id="org-1",
        )

        service.add_fact(
            "incident-1",
            fact_create_command(),
            idempotency_key="fact-audit-idempotent",
            auth_context=auth_context,
            request_id="req-audit-idempotent",
        )
        service.add_fact(
            "incident-1",
            fact_create_command(),
            idempotency_key="fact-audit-idempotent",
            auth_context=auth_context,
            request_id="req-audit-idempotent-second",
        )
        logs = service.list_audit_logs(
            "incident-1",
            action_type="incident.add_fact",
            actor_user_id="user-operator-1",
        )

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].idempotency_key, "fact-audit-idempotent")

    def test_start_replay_run_rejects_idempotency_conflict(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.start_replay_run(
            replay_run_command(),
            idempotency_key="replay-run-conflict",
        )

        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            service.start_replay_run(
                replay_run_command(model_bundle_version="opsgraph-bundle-v2"),
                idempotency_key="replay-run-conflict",
            )

    def test_idempotent_replay_run_does_not_duplicate_audit_log(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        first = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-audit-v1"),
            idempotency_key="replay-run-audit-idempotent",
            auth_context=auth_context,
            request_id="req-replay-run-audit-1",
        )
        second = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-audit-v1"),
            idempotency_key="replay-run-audit-idempotent",
            auth_context=auth_context,
            request_id="req-replay-run-audit-2",
        )
        logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.start_run",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(first.replay_run_id, second.replay_run_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].idempotency_key, "replay-run-audit-idempotent")

    def test_replay_admin_actions_record_audit_logs_and_timeline_actor_context(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        replay = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-audit-admin-v1"),
            idempotency_key="replay-admin-audit-start-1",
            auth_context=auth_context,
            request_id="req-replay-admin-start-1",
        )
        baseline = service.capture_replay_baseline(
            replay_baseline_capture_command(model_bundle_version="opsgraph-audit-admin-v1"),
            auth_context=auth_context,
            request_id="req-replay-admin-baseline-1",
        )
        status = service.update_replay_status(
            replay.replay_run_id,
            replay_status_command(status="running"),
            auth_context=auth_context,
            request_id="req-replay-admin-status-1",
        )
        start_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.start_run",
            actor_user_id="user-admin-1",
        )
        baseline_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.capture_baseline",
            actor_user_id="user-admin-1",
        )
        status_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.update_status",
            actor_user_id="user-admin-1",
        )
        workspace = service.get_incident_workspace("incident-1")
        queued_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "replay_run"
            and item.subject_id == replay.replay_run_id
            and item.kind == "replay_run_queued"
        ]
        running_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "replay_run"
            and item.subject_id == replay.replay_run_id
            and item.kind == "replay_status_updated"
            and item.payload.get("status") == "running"
        ]
        baseline_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "replay_baseline" and item.subject_id == baseline.baseline_id
        ]

        self.assertEqual(status.status, "running")
        self.assertEqual(len(start_logs), 1)
        self.assertEqual(start_logs[0].actor_role, "product_admin")
        self.assertEqual(start_logs[0].request_id, "req-replay-admin-start-1")
        self.assertEqual(start_logs[0].subject_id, replay.replay_run_id)
        self.assertEqual(start_logs[0].result_payload["status"], "queued")
        self.assertEqual(len(baseline_logs), 1)
        self.assertEqual(baseline_logs[0].request_id, "req-replay-admin-baseline-1")
        self.assertEqual(baseline_logs[0].subject_id, baseline.baseline_id)
        self.assertEqual(baseline_logs[0].result_payload["workflow_run_id"], baseline.workflow_run_id)
        self.assertEqual(len(status_logs), 1)
        self.assertEqual(status_logs[0].request_id, "req-replay-admin-status-1")
        self.assertEqual(status_logs[0].request_payload["status"], "running")
        self.assertEqual(status_logs[0].result_payload["status"], "running")
        self.assertEqual(len(queued_timeline), 1)
        self.assertEqual(queued_timeline[0].actor_type, "user")
        self.assertEqual(queued_timeline[0].actor_id, "user-admin-1")
        self.assertEqual(len(running_timeline), 1)
        self.assertEqual(running_timeline[0].actor_id, "user-admin-1")
        self.assertEqual(len(baseline_timeline), 1)
        self.assertEqual(baseline_timeline[0].actor_id, "user-admin-1")

    def test_replay_execute_and_evaluate_record_audit_logs(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        baseline = service.capture_replay_baseline(
            replay_baseline_capture_command(model_bundle_version="opsgraph-audit-eval-v1"),
            auth_context=auth_context,
            request_id="req-replay-eval-baseline-1",
        )
        replay = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-audit-eval-v1"),
            idempotency_key="replay-audit-execute-start-1",
            auth_context=auth_context,
            request_id="req-replay-eval-start-1",
        )
        executed = service.execute_replay_run(
            replay.replay_run_id,
            auth_context=auth_context,
            request_id="req-replay-execute-1",
        )
        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
            auth_context=auth_context,
            request_id="req-replay-evaluate-1",
        )
        execute_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.execute",
            actor_user_id="user-admin-1",
        )
        evaluate_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.evaluate",
            actor_user_id="user-admin-1",
        )
        workspace = service.get_incident_workspace("incident-1")
        completed_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "replay_run"
            and item.subject_id == replay.replay_run_id
            and item.kind == "replay_status_updated"
            and item.payload.get("status") == "completed"
        ]
        evaluation_timeline = [
            item
            for item in workspace.timeline
            if item.subject_type == "replay_evaluation" and item.subject_id == report.report_id
        ]

        self.assertEqual(executed.status, "completed")
        self.assertEqual(len(execute_logs), 1)
        self.assertEqual(execute_logs[0].request_id, "req-replay-execute-1")
        self.assertEqual(execute_logs[0].subject_id, replay.replay_run_id)
        self.assertEqual(execute_logs[0].result_payload["status"], "completed")
        self.assertEqual(execute_logs[0].result_payload["workflow_run_id"], executed.workflow_run_id)
        self.assertEqual(len(evaluate_logs), 1)
        self.assertEqual(evaluate_logs[0].request_id, "req-replay-evaluate-1")
        self.assertEqual(evaluate_logs[0].subject_id, report.report_id)
        self.assertEqual(evaluate_logs[0].request_payload["baseline_id"], baseline.baseline_id)
        self.assertEqual(evaluate_logs[0].result_payload["report_id"], report.report_id)
        self.assertIsNotNone(evaluate_logs[0].result_payload["report_artifact_path"])
        self.assertEqual(len(completed_timeline), 1)
        self.assertEqual(completed_timeline[0].actor_id, "user-admin-1")
        self.assertEqual(len(evaluation_timeline), 1)
        self.assertEqual(evaluation_timeline[0].actor_id, "user-admin-1")

    def test_decide_recommendation_is_idempotent_for_repeated_key(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(),
            idempotency_key="recommendation-decision-1",
        )
        second = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(),
            idempotency_key="recommendation-decision-1",
        )

        self.assertEqual(first.recommendation_id, second.recommendation_id)
        self.assertEqual(first.status, second.status)

    def test_fact_hypothesis_recommendation_comms_and_replay_mutations(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        hypotheses = service.list_hypotheses("incident-1")
        approval_tasks = service.list_approval_tasks("incident-1")
        comms = service.list_comms("incident-1")
        published = service.publish_comms("incident-1", "draft-1", comms_publish_command())
        created_fact = service.add_fact("incident-1", fact_create_command())
        retracted_fact = service.retract_fact("incident-1", created_fact.fact_id, fact_retract_command())
        hypothesis = service.decide_hypothesis("incident-1", "hypothesis-1", hypothesis_decision_command())
        recommendation = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(),
        )
        severity = service.override_severity("incident-1", severity_override_command())
        replay = service.start_replay_run(replay_run_command())
        replay_updated = service.update_replay_status(replay.replay_run_id, replay_status_command())
        replays = service.list_replays("ops-ws-1", "incident-1")
        workspace = service.get_incident_workspace("incident-1")

        self.assertEqual(len(hypotheses), 1)
        self.assertEqual(len(approval_tasks), 1)
        self.assertEqual(approval_tasks[0].approval_task_id, "approval-task-1")
        self.assertEqual(comms[0].created_at.isoformat(), "2026-03-16T09:00:00")
        self.assertEqual(created_fact.status, "confirmed")
        self.assertEqual(retracted_fact.status, "retracted")
        self.assertEqual(hypothesis.status, "accepted")
        self.assertEqual(recommendation.status, "approved")
        self.assertEqual(recommendation.approval_task_id, "approval-task-1")
        self.assertEqual(recommendation.approval_status, "approved")
        self.assertEqual(severity.severity, "sev2")
        self.assertEqual(published.status, "published")
        self.assertEqual(replay.status, "queued")
        self.assertEqual(replay_updated.status, "completed")
        self.assertGreaterEqual(len(replays), 1)
        self.assertGreaterEqual(len(workspace.signals), 1)
        self.assertEqual(len(workspace.hypotheses), 1)
        self.assertEqual(len(workspace.approval_tasks), 1)
        self.assertEqual(workspace.approval_tasks[0].approval_task_id, "approval-task-1")
        self.assertEqual(workspace.recommendations[0].approval_task_id, "approval-task-1")

    def test_add_fact_emits_incident_updated_outbox_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        created_fact = service.add_fact("incident-1", fact_create_command())
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.event_name == "opsgraph.incident.updated"
            and item.event.payload.get("fact_id") == created_fact.fact_id
        ]

        self.assertTrue(any(event.payload.get("mutation") == "fact_added" for event in matching))

    def test_publish_comms_emits_comms_updated_outbox_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        published = service.publish_comms("incident-1", "draft-1", comms_publish_command())
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.event_name == "opsgraph.comms.updated"
            and item.event.payload.get("draft_id") == "draft-1"
        ]

        self.assertEqual(published.status, "published")
        self.assertTrue(any(event.payload.get("comms_status") == "published" for event in matching))

    def test_publish_comms_uses_remote_provider_when_configured(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _RemotePublishClient:
            def __init__(self) -> None:
                self.post_calls: list[dict[str, object]] = []

            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                raise RuntimeError("GET not expected")

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                self.post_calls.append(
                    {
                        "url": url,
                        "headers": dict(headers),
                        "json": dict(json),
                        "follow_redirects": follow_redirects,
                        "timeout": timeout,
                    }
                )

                class _Response:
                    status_code = 200
                    text = ""
                    url = "https://publisher.example.test/messages/internal_slack"

                    @staticmethod
                    def json():
                        return {
                            "published_message_ref": "slack-msg-remote-1",
                            "delivery_state": "published",
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

        fake_client = _RemotePublishClient()
        service.repository.remote_tool_resolver = service.repository.remote_tool_resolver.__class__(
            http_client=fake_client
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://publisher.example.test/messages/{channel_type}",
                "OPSGRAPH_COMMS_PUBLISH_CONNECTION_ID": "slack-publish-http",
            },
            clear=False,
        ):
            published = service.publish_comms("incident-1", "draft-1", comms_publish_command())

        self.assertEqual(published.status, "published")
        self.assertEqual(published.delivery_state, "published")
        self.assertEqual(published.delivery_confirmed, True)
        self.assertEqual(published.provider_delivery_status, "published")
        self.assertEqual(published.published_message_ref, "slack-msg-remote-1")
        self.assertIsNotNone(published.published_at)
        self.assertEqual(fake_client.post_calls[0]["json"]["channel_type"], "internal_slack")
        self.assertIn("Rollback restored availability", str(fake_client.post_calls[0]["json"]["body_markdown"]))

    def test_publish_comms_normalizes_remote_delivery_acceptance(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _RemoteAcceptedClient:
            def __init__(self) -> None:
                self.post_calls: list[dict[str, object]] = []

            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                raise RuntimeError("GET not expected")

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                self.post_calls.append({"url": url, "json": dict(json)})

                class _Response:
                    status_code = 200
                    text = ""
                    url = "https://publisher.example.test/messages/internal_slack"

                    @staticmethod
                    def json():
                        return {
                            "message_id": "slack-msg-accepted-1",
                            "status": "queued",
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

        fake_client = _RemoteAcceptedClient()
        service.repository.remote_tool_resolver = service.repository.remote_tool_resolver.__class__(
            http_client=fake_client
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://publisher.example.test/messages/{channel_type}",
            },
            clear=False,
        ):
            published = service.publish_comms("incident-1", "draft-1", comms_publish_command())

        workspace = service.get_incident_workspace("incident-1")

        self.assertEqual(published.status, "accepted")
        self.assertEqual(published.delivery_state, "accepted")
        self.assertEqual(published.delivery_confirmed, False)
        self.assertEqual(published.provider_delivery_status, "queued")
        self.assertEqual(published.published_message_ref, "slack-msg-accepted-1")
        self.assertIsNone(published.published_at)
        self.assertEqual(workspace.comms_drafts[0].status, "accepted")

    def test_publish_comms_can_confirm_remote_delivery_via_lookup(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _RemoteAcceptedWithLookupClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                self.calls.append({"method": "GET", "url": url})
                response_url = url

                class _Response:
                    def __init__(self) -> None:
                        self.status_code = 200
                        self.text = ""
                        self.url = response_url

                    @staticmethod
                    def json():
                        return {
                            "message_id": "slack-msg-confirmed-1",
                            "status": "published",
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                self.calls.append({"method": "POST", "url": url, "json": dict(json)})

                class _Response:
                    status_code = 200
                    text = ""
                    url = "https://publisher.example.test/messages/internal_slack"

                    @staticmethod
                    def json():
                        return {
                            "message_id": "slack-msg-confirmed-1",
                            "status": "queued",
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

        fake_client = _RemoteAcceptedWithLookupClient()
        service.repository.remote_tool_resolver = service.repository.remote_tool_resolver.__class__(
            http_client=fake_client
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://publisher.example.test/messages/{channel_type}",
                "OPSGRAPH_COMMS_PUBLISH_STATUS_URL_TEMPLATE": (
                    "https://publisher.example.test/messages/{channel_type}/{published_message_ref}/status"
                ),
            },
            clear=False,
        ):
            published = service.publish_comms("incident-1", "draft-1", comms_publish_command())

        workspace = service.get_incident_workspace("incident-1")

        self.assertEqual(published.status, "published")
        self.assertEqual(published.delivery_state, "published")
        self.assertEqual(published.delivery_confirmed, True)
        self.assertEqual(published.provider_delivery_status, "published")
        self.assertEqual(published.published_message_ref, "slack-msg-confirmed-1")
        self.assertIsNotNone(published.published_at)
        self.assertEqual(workspace.comms_drafts[0].status, "published")
        self.assertTrue(
            any(
                call["method"] == "GET"
                and "/slack-msg-confirmed-1/status" in str(call["url"])
                for call in fake_client.calls
            )
        )

    def test_publish_comms_records_remote_delivery_failure_without_marking_published(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _RemoteFailedClient:
            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                raise RuntimeError("GET not expected")

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                class _Response:
                    status_code = 200
                    text = ""
                    url = "https://publisher.example.test/messages/internal_slack"

                    @staticmethod
                    def json():
                        return {
                            "delivery_state": "failed",
                            "delivery_error": {
                                "code": "channel_unavailable",
                                "message": "Slack channel is temporarily unavailable.",
                            },
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

        service.repository.remote_tool_resolver = service.repository.remote_tool_resolver.__class__(
            http_client=_RemoteFailedClient()
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://publisher.example.test/messages/{channel_type}",
            },
            clear=False,
        ):
            published = service.publish_comms("incident-1", "draft-1", comms_publish_command())

        workspace = service.get_incident_workspace("incident-1")

        self.assertEqual(published.status, "failed")
        self.assertEqual(published.delivery_state, "failed")
        self.assertEqual(published.delivery_confirmed, True)
        self.assertEqual(published.provider_delivery_status, "failed")
        self.assertIsNone(published.published_message_ref)
        self.assertIsNone(published.published_at)
        self.assertEqual(
            published.delivery_error,
            {
                "code": "channel_unavailable",
                "message": "Slack channel is temporarily unavailable.",
            },
        )
        self.assertEqual(workspace.comms_drafts[0].status, "failed")

    def test_decide_recommendation_emits_approval_updated_outbox_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        recommendation = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(),
        )
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.event_name == "opsgraph.approval.updated"
            and item.event.payload.get("approval_task_id") == "approval-task-1"
        ]

        self.assertEqual(recommendation.approval_status, "approved")
        self.assertTrue(any(event.payload.get("status") == "approved" for event in matching))

    def test_incident_execution_seed_uses_persisted_signals(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        ingest = service.ingest_alert(alert_ingest_command(source="grafana"))
        seed = service.repository.get_incident_execution_seed("incident-1")

        self.assertIn("signal-1", seed["signal_ids"])
        self.assertIn(ingest.signal_id, seed["signal_ids"])
        self.assertTrue(
            any(item["correlation_key"] == "checkout-api:high-error-rate" for item in seed["signal_summaries"])
        )

    def test_incident_execution_seed_includes_context_bundle_and_memory(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        seed = service.repository.get_incident_execution_seed("incident-1")

        self.assertEqual(seed["context_bundle_id"], "context-incident-1-v1")
        self.assertEqual(seed["context_missing_sources"], [])
        self.assertTrue(seed["investigation_memory_context"])
        self.assertTrue(seed["recommendation_memory_context"])
        self.assertTrue(
            any(item["memory_type"] == "incident_pattern" for item in seed["investigation_memory_context"])
        )
        self.assertTrue(
            any(item["memory_type"] == "successful_mitigation" for item in seed["recommendation_memory_context"])
        )

        with service.repository.session_factory() as session:
            bundle_row = session.get(ContextBundleRow, "context-incident-1-v1")

        self.assertIsNotNone(bundle_row)

    def test_generated_incident_response_binds_comms_to_approval_task(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        ingest = service.ingest_alert(
            alert_ingest_command(
                correlation_key="inventory-api:error-burst",
                summary="Inventory API error burst",
                source="grafana",
            )
        )
        command = service.repository.get_incident_execution_seed(ingest.incident_id) | {
            "workflow_run_id": "opsgraph-generated-incident-1"
        }
        service.respond_to_incident(command)
        workspace = service.get_incident_workspace(ingest.incident_id)

        self.assertEqual(len(workspace.recommendations), 1)
        self.assertEqual(len(workspace.approval_tasks), 1)
        self.assertEqual(len(workspace.comms_drafts), 1)
        self.assertEqual(workspace.incident.service_name, "inventory-api")
        self.assertEqual(workspace.incident.severity, "sev1")
        self.assertIn("inventory-api", workspace.recommendations[0].title.lower())
        self.assertNotEqual(workspace.recommendations[0].title, "Scale checkout workers")
        self.assertEqual(
            workspace.comms_drafts[0].approval_task_id,
            workspace.approval_tasks[0].approval_task_id,
        )

        with self.assertRaisesRegex(ValueError, "APPROVAL_REQUIRED"):
            service.publish_comms(
                ingest.incident_id,
                workspace.comms_drafts[0].draft_id,
                comms_publish_command(
                    expected_fact_set_version=workspace.incident.current_fact_set_version,
                ),
            )

        service.decide_recommendation(
            ingest.incident_id,
            workspace.recommendations[0].recommendation_id,
            recommendation_decision_command(
                approval_task_id=workspace.approval_tasks[0].approval_task_id,
            ),
        )
        published = service.publish_comms(
            ingest.incident_id,
            workspace.comms_drafts[0].draft_id,
            comms_publish_command(
                expected_fact_set_version=workspace.incident.current_fact_set_version,
                approval_task_id=workspace.approval_tasks[0].approval_task_id,
            ),
        )

        self.assertEqual(published.status, "published")

    def test_resolve_incident_persists_successful_mitigation_memory(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        ingest = service.ingest_alert(
            alert_ingest_command(
                correlation_key="catalog-api:error-spike",
                summary="Catalog API error spike",
                source="grafana",
            )
        )
        created_fact = service.add_fact(ingest.incident_id, fact_create_command())
        command = service.repository.get_incident_execution_seed(ingest.incident_id) | {
            "workflow_run_id": "opsgraph-memory-catalog-1"
        }
        service.respond_to_incident(command)
        workspace = service.get_incident_workspace(ingest.incident_id)
        service.decide_recommendation(
            ingest.incident_id,
            workspace.recommendations[0].recommendation_id,
            recommendation_decision_command(
                approval_task_id=workspace.approval_tasks[0].approval_task_id,
            ),
        )

        service.resolve_incident(
            ingest.incident_id,
            resolve_incident_command(
                resolution_summary="Scaled catalog workers and drained the queue.",
            )
            | {"root_cause_fact_ids": [created_fact.fact_id]},
        )
        seed = service.repository.get_incident_execution_seed(ingest.incident_id)

        self.assertTrue(
            any(
                item["memory_type"] == "successful_mitigation"
                and item["summary"] == "Scaled catalog workers and drained the queue."
                for item in seed["recommendation_memory_context"]
            )
        )

        with service.repository.session_factory() as session:
            memory_rows = session.scalars(
                select(MemoryRecordRow)
                .where(MemoryRecordRow.incident_id == ingest.incident_id)
                .where(MemoryRecordRow.memory_type == "successful_mitigation")
            ).all()

        self.assertEqual(len(memory_rows), 1)

    def test_get_runtime_capabilities_reports_product_runtime_backends(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        capabilities = service.get_runtime_capabilities()
        health = service.get_health_status()

        self.assertEqual(capabilities.product, "opsgraph")
        self.assertEqual(capabilities.model_provider.effective_mode, "local")
        self.assertEqual(capabilities.model_provider.backend_id, "heuristic-local")
        self.assertEqual(capabilities.model_provider.fallback_reason, "MODEL_PROVIDER_NOT_CONFIGURED")
        self.assertEqual(capabilities.model_provider.details["fallback_enabled"], True)
        self.assertEqual(capabilities.model_provider.details["fallback_policy_source"], "default")
        self.assertEqual(capabilities.model_provider.details["strict_remote_required"], False)
        self.assertIsNone(capabilities.model_provider.details["last_primary_error"])
        self.assertEqual(capabilities.tooling["incident_store"].backend_id, "sqlalchemy-repository")
        self.assertEqual(capabilities.tooling["deployment_lookup"].backend_id, "heuristic-github-adapter")
        self.assertEqual(capabilities.tooling["service_registry"].backend_id, "heuristic-service-registry")
        self.assertEqual(capabilities.tooling["runbook_search"].effective_mode, "local")
        self.assertIsNotNone(capabilities.auth)
        assert capabilities.auth is not None
        self.assertEqual(capabilities.auth.mode, "demo_compatible")
        self.assertEqual(capabilities.auth.header_fallback_enabled, True)
        self.assertEqual(capabilities.auth.demo_seed_enabled, True)
        self.assertEqual(capabilities.auth.bootstrap_admin_configured, False)
        self.assertIsNotNone(capabilities.runtime_provider_alert)
        assert capabilities.runtime_provider_alert is not None
        self.assertEqual(capabilities.runtime_provider_alert.level, "healthy")
        self.assertEqual(capabilities.runtime_provider_alert.active_alert_count, 0)
        self.assertIsNone(capabilities.replay_worker)
        self.assertIsNotNone(capabilities.replay_worker_alert)
        assert capabilities.replay_worker_alert is not None
        self.assertEqual(capabilities.replay_worker_alert.level, "warning")
        self.assertIsNotNone(capabilities.replay_worker_alert_policy)
        assert capabilities.replay_worker_alert_policy is not None
        self.assertEqual(capabilities.replay_worker_alert_policy.warning_consecutive_failures, 1)
        self.assertEqual(capabilities.replay_worker_alert_policy.critical_consecutive_failures, 3)
        self.assertIsNotNone(health.runtime_summary)
        self.assertEqual(health.runtime_summary.model_provider_mode, "local")
        self.assertEqual(health.runtime_summary.tooling_profile, "product-runtime")
        self.assertEqual(health.runtime_summary.tooling_backends["service_registry"], "heuristic-service-registry")
        self.assertEqual(health.runtime_summary.auth_mode, "demo_compatible")
        self.assertEqual(health.runtime_summary.auth_header_fallback_enabled, True)
        self.assertEqual(health.runtime_summary.auth_demo_seed_enabled, True)
        self.assertEqual(health.runtime_summary.auth_bootstrap_admin_configured, False)
        self.assertEqual(health.runtime_summary.runtime_provider_alert_level, "healthy")
        self.assertEqual(health.runtime_summary.runtime_provider_alert_count, 0)
        self.assertIsNone(health.runtime_summary.replay_worker_status)
        self.assertIsNone(health.runtime_summary.replay_worker_alert_level)

    def test_get_runtime_capabilities_reports_strict_persistent_auth_defaults(self) -> None:
        tmp_dir = create_repo_tempdir("opsgraph-auth-service-")
        database_url = f"sqlite+pysqlite:///{(tmp_dir / 'opsgraph.db').resolve().as_posix()}"

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_ALLOW_HEADER_AUTH_FALLBACK": "",
                "OPSGRAPH_SEED_DEMO_AUTH": "",
                "OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL": "bootstrap-admin@example.com",
                "OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD": "bootstrap-secret",
                "OPSGRAPH_BOOTSTRAP_ADMIN_DISPLAY_NAME": "",
                "OPSGRAPH_BOOTSTRAP_ORG_SLUG": "bootstrap-org",
                "OPSGRAPH_BOOTSTRAP_ORG_NAME": "",
            },
            clear=False,
        ):
            service = build_app_service(database_url=database_url)
        try:
            capabilities = service.get_runtime_capabilities()
            health = service.get_health_status()
        finally:
            service.close()
            cleanup_repo_tempdir(tmp_dir)

        self.assertIsNotNone(capabilities.auth)
        assert capabilities.auth is not None
        self.assertEqual(capabilities.auth.mode, "strict")
        self.assertEqual(capabilities.auth.header_fallback_enabled, False)
        self.assertEqual(capabilities.auth.demo_seed_enabled, False)
        self.assertEqual(capabilities.auth.bootstrap_admin_configured, True)
        self.assertEqual(capabilities.auth.bootstrap_organization_slug, "bootstrap-org")
        self.assertEqual(health.runtime_summary.auth_mode, "strict")
        self.assertEqual(health.runtime_summary.auth_header_fallback_enabled, False)
        self.assertEqual(health.runtime_summary.auth_demo_seed_enabled, False)
        self.assertEqual(health.runtime_summary.auth_bootstrap_admin_configured, True)

    def test_build_app_service_rejects_disabled_model_fallback_without_remote_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_MODEL_PROVIDER": "auto",
                "OPSGRAPH_MODEL_ALLOW_FALLBACK": "false",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "MODEL_PROVIDER_NOT_CONFIGURED"):
                build_app_service()

    def test_get_runtime_capabilities_reports_configured_remote_tool_backends(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_DEPLOYMENT_LOOKUP_PROVIDER": "auto",
                "OPSGRAPH_DEPLOYMENT_LOOKUP_URL_TEMPLATE": (
                    "https://deployments.example.test/services/{service_id}/deployments"
                ),
                "OPSGRAPH_DEPLOYMENT_LOOKUP_BACKEND_ID": "github-deployments-api",
                "OPSGRAPH_SERVICE_REGISTRY_PROVIDER": "auto",
                "OPSGRAPH_SERVICE_REGISTRY_URL_TEMPLATE": (
                    "https://services.example.test/registry?service={service_id}&query={search_query}&limit={limit}"
                ),
                "OPSGRAPH_SERVICE_REGISTRY_BACKEND_ID": "service-registry-api",
                "OPSGRAPH_RUNBOOK_SEARCH_PROVIDER": "auto",
                "OPSGRAPH_RUNBOOK_SEARCH_URL_TEMPLATE": (
                    "https://runbooks.example.test/search?service={service_id}&q={query}&limit={limit}"
                ),
                "OPSGRAPH_RUNBOOK_SEARCH_BACKEND_ID": "runbook-search-api",
            },
            clear=False,
        ):
            service = build_app_service()
            self.addCleanup(service.close)

            capabilities = service.get_runtime_capabilities()

            self.assertEqual(capabilities.tooling["deployment_lookup"].effective_mode, "http")
            self.assertEqual(capabilities.tooling["deployment_lookup"].backend_id, "github-deployments-api")
            self.assertEqual(capabilities.tooling["service_registry"].effective_mode, "http")
            self.assertEqual(capabilities.tooling["service_registry"].backend_id, "service-registry-api")
            self.assertEqual(capabilities.tooling["runbook_search"].effective_mode, "http")
            self.assertEqual(capabilities.tooling["runbook_search"].backend_id, "runbook-search-api")
            self.assertIsNotNone(capabilities.runtime_provider_alert)
            assert capabilities.runtime_provider_alert is not None
            self.assertEqual(capabilities.runtime_provider_alert.level, "healthy")
            self.assertEqual(capabilities.runtime_provider_alert.active_alert_count, 0)
            health = service.get_health_status()
            self.assertEqual(health.runtime_summary.tooling_modes["service_registry"], "http")
            self.assertEqual(health.runtime_summary.tooling_backends["service_registry"], "service-registry-api")
            self.assertEqual(health.runtime_summary.runtime_provider_alert_level, "healthy")
            self.assertEqual(health.runtime_summary.runtime_provider_alert_count, 0)

    def test_get_runtime_capabilities_reports_unavailable_remote_tool_when_fallback_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_RUNBOOK_SEARCH_PROVIDER": "auto",
                "OPSGRAPH_RUNBOOK_SEARCH_ALLOW_FALLBACK": "false",
            },
            clear=False,
        ):
            service = build_app_service()
            self.addCleanup(service.close)

            capabilities = service.get_runtime_capabilities()
            health = service.get_health_status()

        runbook_search = capabilities.tooling["runbook_search"]
        self.assertEqual(runbook_search.requested_mode, "auto")
        self.assertEqual(runbook_search.effective_mode, "unavailable")
        self.assertEqual(runbook_search.backend_id, "http-runbook-provider")
        self.assertEqual(runbook_search.fallback_reason, "OPSGRAPH_RUNBOOK_SEARCH_HTTP_TEMPLATE_NOT_CONFIGURED")
        self.assertEqual(runbook_search.details["fallback_enabled"], False)
        self.assertEqual(runbook_search.details["fallback_policy_source"], "env")
        self.assertEqual(runbook_search.details["strict_remote_required"], True)
        self.assertIsNone(runbook_search.details["last_remote_error"])
        self.assertIsNotNone(capabilities.runtime_provider_alert)
        assert capabilities.runtime_provider_alert is not None
        self.assertEqual(capabilities.runtime_provider_alert.level, "critical")
        self.assertEqual(capabilities.runtime_provider_alert.active_alert_count, 1)
        self.assertEqual(capabilities.runtime_provider_alert.alerts[0].capability_name, "runbook_search")
        self.assertEqual(
            capabilities.runtime_provider_alert.alerts[0].reason_code,
            "OPSGRAPH_RUNBOOK_SEARCH_HTTP_TEMPLATE_NOT_CONFIGURED",
        )
        self.assertEqual(health.runtime_summary.runtime_provider_alert_level, "critical")
        self.assertEqual(health.runtime_summary.runtime_provider_alert_count, 1)

    def test_get_runtime_capabilities_reports_configured_replay_worker_alert_policy(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_REPLAY_ALERT_WARNING_CONSECUTIVE_FAILURES": "2",
                "OPSGRAPH_REPLAY_ALERT_CRITICAL_CONSECUTIVE_FAILURES": "4",
            },
            clear=False,
        ):
            service = build_app_service()
            self.addCleanup(service.close)

            capabilities = service.get_runtime_capabilities()

            self.assertIsNotNone(capabilities.replay_worker_alert_policy)
            assert capabilities.replay_worker_alert_policy is not None
            self.assertEqual(capabilities.replay_worker_alert_policy.warning_consecutive_failures, 2)
            self.assertEqual(capabilities.replay_worker_alert_policy.critical_consecutive_failures, 4)

    def test_run_remote_provider_smoke_reports_local_fallback_defaults_without_remote_configuration(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        smoke = service.run_remote_provider_smoke({})

        self.assertEqual(
            smoke.providers,
            ["deployment_lookup", "service_registry", "runbook_search", "change_context"],
        )
        self.assertEqual(smoke.summary.success_count, 4)
        self.assertEqual(smoke.summary.skipped_count, 0)
        self.assertEqual(smoke.summary.failed_count, 0)
        self.assertEqual(smoke.exit_code, 0)
        self.assertTrue(str(smoke.diagnostic_run_id).startswith("runtime-smoke-"))
        self.assertIsNotNone(smoke.created_at)
        self.assertTrue(all(result.status == "success" for result in smoke.results))
        self.assertTrue(all(result.execution_mode == "local_fallback" for result in smoke.results))

    def test_run_remote_provider_smoke_respects_require_configured_for_local_fallback_provider(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        smoke = service.run_remote_provider_smoke(
            {
                "providers": ["deployment_lookup"],
                "require_configured": True,
            }
        )

        self.assertEqual(smoke.summary.success_count, 1)
        self.assertEqual(smoke.summary.skipped_count, 0)
        self.assertEqual(smoke.summary.failed_count, 0)
        self.assertEqual(smoke.exit_code, 1)
        self.assertEqual(smoke.results[0].provider, "deployment_lookup")
        self.assertEqual(smoke.results[0].status, "success")
        self.assertEqual(smoke.results[0].execution_mode, "local_fallback")

    def test_run_remote_provider_smoke_persists_history_with_actor_context(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )
        smoke = service.run_remote_provider_smoke(
            {"providers": ["deployment_lookup"]},
            auth_context=auth_context,
            request_id="req-runtime-smoke-1",
        )
        history = service.list_remote_provider_smoke_runs(limit=1)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].diagnostic_run_id, smoke.diagnostic_run_id)
        self.assertEqual(history[0].actor_type, "user")
        self.assertEqual(history[0].actor_user_id, "user-admin-1")
        self.assertEqual(history[0].actor_role, "product_admin")
        self.assertEqual(history[0].request_id, "req-runtime-smoke-1")
        self.assertEqual(history[0].request_payload["providers"], ["deployment_lookup"])
        self.assertEqual(history[0].response.summary.success_count, 1)
        self.assertEqual(history[0].response.results[0].execution_mode, "local_fallback")

    def test_list_remote_provider_smoke_runs_supports_actor_request_and_provider_filters(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        admin_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )
        first = service.run_remote_provider_smoke(
            {"providers": ["deployment_lookup"]},
            auth_context=admin_context,
            request_id="req-runtime-smoke-filter-admin",
        )
        second = service.run_remote_provider_smoke(
            {"providers": ["runbook_search"]},
            request_id="req-runtime-smoke-filter-system",
        )

        by_actor = service.list_remote_provider_smoke_runs(
            limit=10,
            actor_user_id="user-admin-1",
        )
        by_request = service.list_remote_provider_smoke_runs(
            limit=10,
            request_id="req-runtime-smoke-filter-system",
        )
        by_provider = service.list_remote_provider_smoke_runs(
            limit=10,
            provider="runbook_search",
        )

        self.assertEqual([item.diagnostic_run_id for item in by_actor], [first.diagnostic_run_id])
        self.assertEqual([item.diagnostic_run_id for item in by_request], [second.diagnostic_run_id])
        self.assertEqual([item.diagnostic_run_id for item in by_provider], [second.diagnostic_run_id])

    def test_list_remote_provider_smoke_runs_rejects_invalid_limit(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "INVALID_REMOTE_PROVIDER_SMOKE_HISTORY_LIMIT"):
            service.list_remote_provider_smoke_runs(limit=0)

    def test_summarize_remote_provider_smoke_runs_returns_provider_aggregates(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        first = service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["deployment_lookup", "runbook_search"]},
            response_payload={
                "providers": ["deployment_lookup", "runbook_search"],
                "summary": {"success_count": 1, "skipped_count": 1, "failed_count": 0},
                "results": [
                    {
                        "provider": "deployment_lookup",
                        "status": "success",
                        "reason": None,
                        "capability": {
                            "requested_mode": "http",
                            "effective_mode": "http",
                            "backend_id": "http-deployment-provider",
                            "fallback_reason": None,
                            "details": {"strict_remote_required": True},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": {"deployments": []},
                        "provenance": None,
                    },
                    {
                        "provider": "runbook_search",
                        "status": "skipped",
                        "reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-runbook-index",
                            "fallback_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    },
                ],
                "exit_code": 0,
            },
        )
        second = service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["deployment_lookup"]},
            response_payload={
                "providers": ["deployment_lookup"],
                "summary": {"success_count": 0, "skipped_count": 0, "failed_count": 1},
                "results": [
                    {
                        "provider": "deployment_lookup",
                        "status": "failed",
                        "reason": "RuntimeError",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-github-adapter",
                            "fallback_reason": "OPSGRAPH_DEPLOYMENT_LOOKUP_REMOTE_REQUEST_FAILED",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    }
                ],
                "exit_code": 1,
            },
        )

        summary = service.summarize_remote_provider_smoke_runs(limit=10)
        deployment_summary = next(item for item in summary.providers if item.provider == "deployment_lookup")
        runbook_summary = next(item for item in summary.providers if item.provider == "runbook_search")

        self.assertEqual(summary.scanned_run_count, 2)
        self.assertEqual(summary.provider_count, 2)
        self.assertEqual(deployment_summary.run_count, 2)
        self.assertEqual(deployment_summary.success_count, 1)
        self.assertEqual(deployment_summary.failed_count, 1)
        self.assertEqual(deployment_summary.skipped_count, 0)
        self.assertEqual(deployment_summary.consecutive_failure_count, 1)
        self.assertEqual(deployment_summary.consecutive_non_success_count, 1)
        self.assertEqual(deployment_summary.last_status, "failed")
        self.assertEqual(deployment_summary.last_reason, "RuntimeError")
        self.assertEqual(deployment_summary.last_diagnostic_run_id, second.diagnostic_run_id)
        self.assertIsNotNone(deployment_summary.last_success_at)
        self.assertIsNotNone(deployment_summary.last_failure_at)
        self.assertEqual(runbook_summary.run_count, 1)
        self.assertEqual(runbook_summary.skipped_count, 1)
        self.assertEqual(runbook_summary.consecutive_failure_count, 0)
        self.assertEqual(runbook_summary.consecutive_non_success_count, 1)
        self.assertEqual(runbook_summary.last_status, "skipped")
        self.assertEqual(runbook_summary.last_diagnostic_run_id, first.diagnostic_run_id)

    def test_summarize_remote_provider_smoke_runs_supports_provider_filter(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["deployment_lookup"]},
            response_payload={
                "providers": ["deployment_lookup"],
                "summary": {"success_count": 0, "skipped_count": 1, "failed_count": 0},
                "results": [
                    {
                        "provider": "deployment_lookup",
                        "status": "skipped",
                        "reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-github-adapter",
                            "fallback_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    }
                ],
                "exit_code": 0,
            },
        )
        service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["runbook_search"]},
            response_payload={
                "providers": ["runbook_search"],
                "summary": {"success_count": 0, "skipped_count": 1, "failed_count": 0},
                "results": [
                    {
                        "provider": "runbook_search",
                        "status": "skipped",
                        "reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-runbook-index",
                            "fallback_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    }
                ],
                "exit_code": 0,
            },
        )

        summary = service.summarize_remote_provider_smoke_runs(
            limit=10,
            provider="runbook_search",
        )

        self.assertEqual(summary.scanned_run_count, 1)
        self.assertEqual(summary.provider_count, 1)
        self.assertEqual(summary.providers[0].provider, "runbook_search")

    def test_get_runtime_capabilities_reports_healthy_remote_provider_smoke_alert_without_failures(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        capabilities = service.get_runtime_capabilities()
        health = service.get_health_status()

        self.assertIsNotNone(capabilities.remote_provider_smoke_alert)
        assert capabilities.remote_provider_smoke_alert is not None
        self.assertEqual(capabilities.remote_provider_smoke_alert.level, "healthy")
        self.assertEqual(capabilities.remote_provider_smoke_alert.active_alert_count, 0)
        self.assertEqual(health.runtime_summary.remote_provider_smoke_alert_level, "healthy")
        self.assertEqual(health.runtime_summary.remote_provider_smoke_alert_count, 0)

    def test_get_runtime_capabilities_reports_warning_and_critical_remote_provider_smoke_alerts(self) -> None:
        service = build_app_service(
            remote_provider_smoke_alert_warning_consecutive_failures=1,
            remote_provider_smoke_alert_critical_consecutive_failures=2,
        )
        self.addCleanup(service.close)

        service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["deployment_lookup"]},
            response_payload={
                "providers": ["deployment_lookup"],
                "summary": {"success_count": 0, "skipped_count": 0, "failed_count": 1},
                "results": [
                    {
                        "provider": "deployment_lookup",
                        "status": "failed",
                        "reason": "RuntimeError",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-github-adapter",
                            "fallback_reason": "REMOTE_REQUEST_FAILED",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    }
                ],
                "exit_code": 1,
            },
        )
        warning_capabilities = service.get_runtime_capabilities()
        warning_health = service.get_health_status()

        self.assertIsNotNone(warning_capabilities.remote_provider_smoke_alert)
        assert warning_capabilities.remote_provider_smoke_alert is not None
        self.assertEqual(warning_capabilities.remote_provider_smoke_alert.level, "warning")
        self.assertEqual(warning_capabilities.remote_provider_smoke_alert.active_alert_count, 1)
        self.assertEqual(
            warning_capabilities.remote_provider_smoke_alert.alerts[0].consecutive_failure_count,
            1,
        )
        self.assertEqual(warning_health.runtime_summary.remote_provider_smoke_alert_level, "warning")

        service.repository.record_remote_provider_smoke_run(
            request_payload={"providers": ["deployment_lookup"]},
            response_payload={
                "providers": ["deployment_lookup"],
                "summary": {"success_count": 0, "skipped_count": 0, "failed_count": 1},
                "results": [
                    {
                        "provider": "deployment_lookup",
                        "status": "failed",
                        "reason": "TimeoutError",
                        "capability": {
                            "requested_mode": "auto",
                            "effective_mode": "local",
                            "backend_id": "heuristic-github-adapter",
                            "fallback_reason": "REMOTE_REQUEST_FAILED",
                            "details": {"strict_remote_required": False},
                        },
                        "request": {"service_id": "checkout-api"},
                        "response": None,
                        "provenance": None,
                    }
                ],
                "exit_code": 1,
            },
        )
        critical_capabilities = service.get_runtime_capabilities()
        critical_health = service.get_health_status()

        self.assertEqual(critical_capabilities.remote_provider_smoke_alert.level, "critical")
        self.assertEqual(critical_capabilities.remote_provider_smoke_alert.active_alert_count, 1)
        self.assertEqual(
            critical_capabilities.remote_provider_smoke_alert.alerts[0].consecutive_failure_count,
            2,
        )
        self.assertEqual(
            critical_capabilities.remote_provider_smoke_alert.alerts[0].reason_code,
            "TimeoutError",
        )
        self.assertEqual(critical_health.runtime_summary.remote_provider_smoke_alert_level, "critical")
        self.assertEqual(critical_health.runtime_summary.remote_provider_smoke_alert_count, 1)

    def test_get_runtime_capabilities_reports_warning_after_remote_tool_error_with_fallback(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _FailingRemoteReadClient:
            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                del url, headers, follow_redirects, timeout
                raise RuntimeError("provider unavailable")

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                del url, headers, json, follow_redirects, timeout
                raise RuntimeError("POST not expected")

        service.repository.remote_tool_resolver._http_client = _FailingRemoteReadClient()

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_RUNBOOK_SEARCH_PROVIDER": "auto",
                "OPSGRAPH_RUNBOOK_SEARCH_URL_TEMPLATE": (
                    "https://runbooks.example.test/search?service={service_id}&q={query}&limit={limit}"
                ),
            },
            clear=False,
        ):
            smoke = service.run_remote_provider_smoke({"providers": ["runbook_search"]})
            capabilities = service.get_runtime_capabilities()
            health = service.get_health_status()

        self.assertEqual(smoke.summary.failed_count, 1)
        self.assertEqual(smoke.results[0].status, "failed")
        self.assertIsNotNone(capabilities.runtime_provider_alert)
        assert capabilities.runtime_provider_alert is not None
        self.assertEqual(capabilities.runtime_provider_alert.level, "warning")
        self.assertEqual(capabilities.runtime_provider_alert.active_alert_count, 1)
        self.assertEqual(capabilities.runtime_provider_alert.alerts[0].capability_name, "runbook_search")
        self.assertEqual(capabilities.runtime_provider_alert.alerts[0].reason_code, "RuntimeError")
        self.assertEqual(health.runtime_summary.runtime_provider_alert_level, "warning")
        self.assertEqual(health.runtime_summary.runtime_provider_alert_count, 1)

    def test_get_runtime_capabilities_uses_workspace_override_for_latest_worker_policy(self) -> None:
        service = build_app_service(
            replay_worker_alert_warning_consecutive_failures=1,
            replay_worker_alert_critical_consecutive_failures=5,
        )
        self.addCleanup(service.close)
        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 2,
            },
        )
        service.repository.record_replay_worker_heartbeat(
            workspace_id="ops-ws-1",
            status="retrying",
            iteration=4,
            attempted_count=0,
            dispatched_count=0,
            failed_count=2,
            skipped_count=0,
            idle_polls=0,
            consecutive_failures=2,
            remaining_queued_count=1,
            error_message=None,
            emitted_at=datetime(2026, 3, 27, 9, 30, tzinfo=UTC),
        )

        capabilities = service.get_runtime_capabilities()

        self.assertIsNotNone(capabilities.replay_worker_alert_policy)
        assert capabilities.replay_worker_alert_policy is not None
        self.assertEqual(capabilities.replay_worker_alert_policy.workspace_id, "ops-ws-1")
        self.assertEqual(capabilities.replay_worker_alert_policy.source, "workspace_override")
        self.assertEqual(capabilities.replay_worker_alert_policy.critical_consecutive_failures, 2)
        self.assertIsNotNone(capabilities.replay_worker_alert)
        assert capabilities.replay_worker_alert is not None
        self.assertEqual(capabilities.replay_worker_alert.level, "critical")

    def test_get_runtime_capabilities_reports_last_replay_worker_heartbeat(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        worker = OpsGraphReplayWorker(service)

        service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-worker-health-v1"),
            idempotency_key="opsgraph-worker-health-1",
        )
        worker.build_supervisor().run(
            poll_interval_seconds=0,
            max_iterations=2,
            max_idle_polls=1,
            heartbeat_every_iterations=1,
        )

        capabilities = service.get_runtime_capabilities()
        health = service.get_health_status()

        self.assertIsNotNone(capabilities.replay_worker)
        assert capabilities.replay_worker is not None
        self.assertEqual(capabilities.replay_worker.workspace_id, "ops-ws-1")
        self.assertEqual(capabilities.replay_worker.status, "idle")
        self.assertEqual(capabilities.replay_worker.remaining_queued_count, 0)
        self.assertEqual([item.status for item in capabilities.replay_worker_history], ["idle", "active"])
        self.assertIsNotNone(capabilities.replay_worker_alert)
        assert capabilities.replay_worker_alert is not None
        self.assertEqual(capabilities.replay_worker_alert.level, "healthy")
        self.assertEqual(health.runtime_summary.replay_worker_status, "idle")
        self.assertEqual(health.runtime_summary.replay_worker_workspace_id, "ops-ws-1")
        self.assertEqual(health.runtime_summary.replay_worker_remaining_queued_count, 0)
        self.assertIsNotNone(health.runtime_summary.replay_worker_last_seen_at)
        self.assertEqual(health.runtime_summary.replay_worker_alert_level, "healthy")

    def test_get_replay_worker_status_returns_current_and_history_window(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        worker = OpsGraphReplayWorker(service)

        service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-worker-route-v1"),
            idempotency_key="opsgraph-worker-route-1",
        )
        worker.build_supervisor().run(
            poll_interval_seconds=0,
            max_iterations=3,
            max_idle_polls=2,
            heartbeat_every_iterations=1,
        )

        status = service.get_replay_worker_status(workspace_id="ops-ws-1", history_limit=2)

        self.assertEqual(status.workspace_id, "ops-ws-1")
        self.assertIsNotNone(status.current)
        assert status.current is not None
        self.assertEqual(status.current.status, "idle")
        self.assertEqual([item.status for item in status.history], ["idle", "idle"])
        self.assertIsNotNone(status.alert)
        assert status.alert is not None
        self.assertEqual(status.alert.level, "healthy")
        self.assertIn("healthy", status.alert.headline.lower())
        self.assertIsNotNone(status.policy)
        assert status.policy is not None
        self.assertEqual(status.policy.workspace_id, "ops-ws-1")
        self.assertEqual(status.policy.source, "default")
        self.assertEqual(status.policy.default_warning_consecutive_failures, 1)
        self.assertEqual(status.policy.default_critical_consecutive_failures, 3)

    def test_get_replay_worker_status_rejects_invalid_history_limit(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "INVALID_REPLAY_WORKER_HISTORY_LIMIT"):
            service.get_replay_worker_status(history_limit=0)

    def test_get_replay_worker_status_surfaces_recent_failure_alert(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.repository.record_replay_worker_heartbeat(
            workspace_id="ops-ws-1",
            status="retrying",
            iteration=7,
            attempted_count=0,
            dispatched_count=0,
            failed_count=1,
            skipped_count=0,
            idle_polls=0,
            consecutive_failures=1,
            remaining_queued_count=2,
            error_message="transient worker failure",
            emitted_at=datetime(2026, 3, 27, 10, 0, tzinfo=UTC),
        )
        service.repository.record_replay_worker_heartbeat(
            workspace_id="ops-ws-1",
            status="idle",
            iteration=8,
            attempted_count=0,
            dispatched_count=0,
            failed_count=0,
            skipped_count=0,
            idle_polls=1,
            consecutive_failures=0,
            remaining_queued_count=0,
            error_message=None,
            emitted_at=datetime(2026, 3, 27, 10, 0, 5, tzinfo=UTC),
        )

        status = service.get_replay_worker_status(workspace_id="ops-ws-1", history_limit=5)

        self.assertIsNotNone(status.alert)
        assert status.alert is not None
        self.assertEqual(status.alert.level, "warning")
        self.assertEqual(status.alert.latest_failure_status, "retrying")
        self.assertEqual(status.alert.latest_failure_message, "transient worker failure")
        self.assertEqual(
            status.alert.latest_failure_at.replace(tzinfo=None) if status.alert.latest_failure_at else None,
            datetime(2026, 3, 27, 10, 0),
        )

    def test_update_replay_worker_alert_policy_persists_workspace_override(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        updated = service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
            },
        )
        fetched = service.get_replay_worker_alert_policy("ops-ws-1")

        self.assertEqual(updated.workspace_id, "ops-ws-1")
        self.assertEqual(updated.warning_consecutive_failures, 2)
        self.assertEqual(updated.critical_consecutive_failures, 4)
        self.assertEqual(updated.source, "workspace_override")
        self.assertIsNotNone(updated.updated_at)
        self.assertEqual(updated.default_warning_consecutive_failures, 1)
        self.assertEqual(updated.default_critical_consecutive_failures, 3)
        self.assertEqual(fetched.workspace_id, "ops-ws-1")
        self.assertEqual(fetched.source, "workspace_override")
        self.assertEqual(fetched.critical_consecutive_failures, 4)

    def test_update_replay_worker_alert_policy_records_replay_admin_audit_log(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
            },
            auth_context=auth_context,
            request_id="req-policy-audit-1",
        )

        logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.update_worker_alert_policy",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].workspace_id, "ops-ws-1")
        self.assertEqual(logs[0].subject_type, "replay_worker_alert_policy")
        self.assertEqual(logs[0].subject_id, "ops-ws-1")
        self.assertEqual(logs[0].request_id, "req-policy-audit-1")
        self.assertEqual(logs[0].actor_role, "product_admin")
        self.assertEqual(logs[0].request_payload["warning_consecutive_failures"], 2)
        self.assertEqual(logs[0].result_payload["source"], "workspace_override")

    def test_list_replay_admin_audit_logs_supports_request_id_filter(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
            },
            auth_context=auth_context,
            request_id="req-policy-audit-1",
        )
        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 3,
                "critical_consecutive_failures": 5,
            },
            auth_context=auth_context,
            request_id="req-policy-audit-2",
        )

        logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.update_worker_alert_policy",
            request_id="req-policy-audit-1",
        )

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].request_id, "req-policy-audit-1")
        self.assertEqual(logs[0].request_payload["warning_consecutive_failures"], 2)

    def test_update_replay_worker_alert_policy_matching_default_resets_override(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
            },
        )
        reset = service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 1,
                "critical_consecutive_failures": 3,
            },
        )

        self.assertEqual(reset.workspace_id, "ops-ws-1")
        self.assertEqual(reset.source, "default")
        self.assertIsNone(reset.updated_at)
        self.assertIsNone(service.repository.get_replay_worker_alert_policy("ops-ws-1"))

    def test_upsert_and_list_replay_worker_monitor_presets(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        updated = service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            {
                "history_limit": 15,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-1",
                "policy_audit_limit": 20,
                "policy_audit_copy_format": "slack",
                "policy_audit_include_summary": False,
            },
        )
        presets = service.list_replay_worker_monitor_presets("ops-ws-1")

        self.assertEqual(updated.workspace_id, "ops-ws-1")
        self.assertEqual(updated.preset_name, "night-shift")
        self.assertEqual(updated.history_limit, 15)
        self.assertEqual(updated.policy_audit_limit, 20)
        self.assertEqual(updated.policy_audit_copy_format, "slack")
        self.assertFalse(updated.policy_audit_include_summary)
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0].preset_name, "night-shift")
        self.assertEqual(presets[0].actor_user_id, "user-admin-1")

    def test_upsert_replay_worker_monitor_preset_records_replay_admin_audit_log(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            {
                "history_limit": 10,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-1",
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "markdown",
                "policy_audit_include_summary": True,
            },
            auth_context=auth_context,
            request_id="req-monitor-preset-audit-1",
        )

        logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.upsert_worker_monitor_preset",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].subject_type, "replay_worker_monitor_preset")
        self.assertEqual(logs[0].subject_id, "ops-ws-1:night-shift")
        self.assertEqual(logs[0].request_id, "req-monitor-preset-audit-1")
        self.assertEqual(logs[0].request_payload["preset_name"], "night-shift")
        self.assertEqual(logs[0].result_payload["policy_audit_copy_format"], "markdown")

    def test_delete_replay_worker_monitor_preset_removes_workspace_preset_and_records_audit_log(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )
        service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            {
                "history_limit": 10,
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "plain",
                "policy_audit_include_summary": True,
            },
        )

        deleted = service.delete_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            auth_context=auth_context,
            request_id="req-monitor-preset-delete-1",
        )
        presets = service.list_replay_worker_monitor_presets("ops-ws-1")
        logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.delete_worker_monitor_preset",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(deleted.workspace_id, "ops-ws-1")
        self.assertEqual(deleted.preset_name, "night-shift")
        self.assertTrue(deleted.deleted)
        self.assertEqual(presets, [])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].subject_type, "replay_worker_monitor_preset")
        self.assertEqual(logs[0].subject_id, "ops-ws-1:night-shift")
        self.assertEqual(logs[0].request_id, "req-monitor-preset-delete-1")
        self.assertTrue(logs[0].result_payload["deleted"])

    def test_set_and_clear_replay_worker_monitor_default_preset(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "baseline",
            {
                "history_limit": 8,
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "plain",
                "policy_audit_include_summary": True,
            },
        )
        service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            {
                "history_limit": 10,
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "plain",
                "policy_audit_include_summary": True,
            },
        )

        workspace_default = service.set_replay_worker_monitor_default_preset("ops-ws-1", "baseline")
        shift_default = service.set_replay_worker_monitor_default_preset(
            "ops-ws-1",
            "night-shift",
            shift_label="night",
        )
        workspace_presets = service.list_replay_worker_monitor_presets("ops-ws-1")
        night_presets = service.list_replay_worker_monitor_presets("ops-ws-1", shift_label="night")
        day_default = service.get_replay_worker_monitor_default_preset("ops-ws-1", shift_label="day")
        cleared = service.clear_replay_worker_monitor_default_preset("ops-ws-1", shift_label="night")
        cleared_night_presets = service.list_replay_worker_monitor_presets("ops-ws-1", shift_label="night")

        self.assertEqual(workspace_default.workspace_id, "ops-ws-1")
        self.assertEqual(workspace_default.preset_name, "baseline")
        self.assertEqual(workspace_default.source, "workspace_default")
        self.assertEqual(shift_default.workspace_id, "ops-ws-1")
        self.assertEqual(shift_default.preset_name, "night-shift")
        self.assertEqual(shift_default.shift_label, "night")
        self.assertEqual(shift_default.source, "shift_default")
        self.assertFalse(shift_default.cleared)
        self.assertEqual(workspace_presets[0].preset_name, "baseline")
        self.assertTrue(workspace_presets[0].is_default)
        self.assertEqual(workspace_presets[0].default_source, "workspace_default")
        self.assertEqual(night_presets[1].preset_name, "night-shift")
        self.assertTrue(night_presets[1].is_default)
        self.assertEqual(night_presets[1].default_source, "shift_default")
        self.assertEqual(day_default.preset_name, "baseline")
        self.assertEqual(day_default.shift_label, "day")
        self.assertEqual(day_default.source, "workspace_default")
        self.assertEqual(cleared.workspace_id, "ops-ws-1")
        self.assertEqual(cleared.preset_name, "night-shift")
        self.assertEqual(cleared.shift_label, "night")
        self.assertEqual(cleared.source, "shift_default")
        self.assertTrue(cleared.cleared)
        self.assertEqual(cleared_night_presets[0].preset_name, "baseline")
        self.assertTrue(cleared_night_presets[0].is_default)
        self.assertEqual(cleared_night_presets[0].default_source, "workspace_default")
        self.assertEqual(cleared_night_presets[1].preset_name, "night-shift")
        self.assertFalse(cleared_night_presets[1].is_default)

    def test_set_replay_worker_monitor_default_preset_records_replay_admin_audit_logs(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )
        service.upsert_replay_worker_monitor_preset(
            "ops-ws-1",
            "night-shift",
            {
                "history_limit": 10,
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "plain",
                "policy_audit_include_summary": True,
            },
        )

        service.set_replay_worker_monitor_default_preset(
            "ops-ws-1",
            "night-shift",
            shift_label="night",
            auth_context=auth_context,
            request_id="req-monitor-default-1",
        )
        service.clear_replay_worker_monitor_default_preset(
            "ops-ws-1",
            shift_label="night",
            auth_context=auth_context,
            request_id="req-monitor-default-2",
        )
        set_logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.set_worker_monitor_default_preset",
            actor_user_id="user-admin-1",
        )
        clear_logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.clear_worker_monitor_default_preset",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(len(set_logs), 1)
        self.assertEqual(set_logs[0].subject_type, "replay_worker_monitor_preset_default")
        self.assertEqual(set_logs[0].request_id, "req-monitor-default-1")
        self.assertEqual(set_logs[0].request_payload["shift_label"], "night")
        self.assertEqual(set_logs[0].result_payload["preset_name"], "night-shift")
        self.assertEqual(set_logs[0].result_payload["source"], "shift_default")
        self.assertEqual(len(clear_logs), 1)
        self.assertEqual(clear_logs[0].subject_type, "replay_worker_monitor_preset_default")
        self.assertEqual(clear_logs[0].request_id, "req-monitor-default-2")
        self.assertEqual(clear_logs[0].request_payload["shift_label"], "night")
        self.assertEqual(clear_logs[0].result_payload["source"], "shift_default")
        self.assertTrue(clear_logs[0].result_payload["cleared"])

    def test_update_and_resolve_replay_worker_monitor_shift_schedule(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        updated = service.update_replay_worker_monitor_shift_schedule(
            "ops-ws-1",
            {
                "timezone": "UTC",
                "windows": [
                    {"shift_label": "day", "start_time": "08:00", "end_time": "20:00"},
                    {"shift_label": "night", "start_time": "20:00", "end_time": "08:00"},
                ],
                "date_overrides": [
                    {
                        "date": "2026-03-27",
                        "note": "Holiday coverage",
                        "windows": [
                            {"shift_label": "holiday", "start_time": "10:00", "end_time": "14:00"},
                        ],
                    }
                ],
                "date_range_overrides": [
                    {
                        "start_date": "2026-03-29",
                        "end_date": "2026-03-31",
                        "note": "Migration week",
                        "windows": [
                            {"shift_label": "migration", "start_time": "09:00", "end_time": "18:00"},
                        ],
                    }
                ],
            },
        )
        current = service.get_replay_worker_monitor_shift_schedule("ops-ws-1")
        resolved_override = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 27, 11, 30, tzinfo=UTC),
        )
        resolved_override_gap = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 27, 15, 0, tzinfo=UTC),
        )
        resolved_range = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 30, 10, 30, tzinfo=UTC),
        )
        resolved_range_gap = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 30, 22, 0, tzinfo=UTC),
        )
        resolved_day = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 28, 9, 30, tzinfo=UTC),
        )
        resolved_night = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 28, 21, 30, tzinfo=UTC),
        )
        cleared = service.clear_replay_worker_monitor_shift_schedule("ops-ws-1")
        resolved_none = service.resolve_replay_worker_monitor_shift_label(
            "ops-ws-1",
            evaluated_at=datetime(2026, 3, 28, 21, 30, tzinfo=UTC),
        )

        self.assertEqual(updated.workspace_id, "ops-ws-1")
        self.assertEqual(updated.timezone, "UTC")
        self.assertEqual(len(updated.windows), 2)
        self.assertEqual(len(updated.date_overrides), 1)
        self.assertEqual(len(updated.date_range_overrides), 1)
        self.assertEqual(current.windows[0].shift_label, "day")
        self.assertEqual(current.date_overrides[0].date, "2026-03-27")
        self.assertEqual(current.date_range_overrides[0].start_date, "2026-03-29")
        self.assertEqual(resolved_override.shift_label, "holiday")
        self.assertEqual(resolved_override.source, "date_override")
        self.assertEqual(resolved_override.override_date, "2026-03-27")
        self.assertEqual(resolved_override.override_note, "Holiday coverage")
        self.assertEqual(resolved_override_gap.source, "date_override")
        self.assertIsNone(resolved_override_gap.shift_label)
        self.assertEqual(resolved_range.shift_label, "migration")
        self.assertEqual(resolved_range.source, "date_range_override")
        self.assertEqual(resolved_range.override_range_start_date, "2026-03-29")
        self.assertEqual(resolved_range.override_range_end_date, "2026-03-31")
        self.assertEqual(resolved_range.override_note, "Migration week")
        self.assertEqual(resolved_range_gap.source, "date_range_override")
        self.assertIsNone(resolved_range_gap.shift_label)
        self.assertEqual(resolved_day.shift_label, "day")
        self.assertEqual(resolved_day.source, "schedule")
        self.assertEqual(resolved_day.matched_window.shift_label, "day")
        self.assertEqual(resolved_night.shift_label, "night")
        self.assertEqual(resolved_night.source, "schedule")
        self.assertEqual(cleared.workspace_id, "ops-ws-1")
        self.assertTrue(cleared.cleared)
        self.assertIsNone(resolved_none.shift_label)
        self.assertEqual(resolved_none.source, "none")

    def test_update_and_clear_replay_worker_monitor_shift_schedule_records_replay_admin_audit_logs(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )

        service.update_replay_worker_monitor_shift_schedule(
            "ops-ws-1",
            {
                "timezone": "Asia/Shanghai",
                "windows": [
                    {"shift_label": "day", "start_time": "09:00", "end_time": "21:00"},
                    {"shift_label": "night", "start_time": "21:00", "end_time": "09:00"},
                ],
                "date_overrides": [
                    {
                        "date": "2026-03-27",
                        "note": "Temporary rotation",
                        "windows": [
                            {"shift_label": "sre", "start_time": "09:00", "end_time": "17:00"},
                        ],
                    }
                ],
                "date_range_overrides": [
                    {
                        "start_date": "2026-03-28",
                        "end_date": "2026-03-30",
                        "note": "Release week",
                        "windows": [
                            {"shift_label": "release", "start_time": "08:00", "end_time": "20:00"},
                        ],
                    }
                ],
            },
            auth_context=auth_context,
            request_id="req-monitor-shift-schedule-1",
        )
        service.clear_replay_worker_monitor_shift_schedule(
            "ops-ws-1",
            auth_context=auth_context,
            request_id="req-monitor-shift-schedule-2",
        )
        update_logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.update_worker_monitor_shift_schedule",
            actor_user_id="user-admin-1",
        )
        clear_logs = service.list_replay_admin_audit_logs(
            "ops-ws-1",
            action_type="replay.clear_worker_monitor_shift_schedule",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(len(update_logs), 1)
        self.assertEqual(update_logs[0].request_id, "req-monitor-shift-schedule-1")
        self.assertEqual(update_logs[0].request_payload["timezone"], "Asia/Shanghai")
        self.assertEqual(update_logs[0].request_payload["date_overrides"][0]["date"], "2026-03-27")
        self.assertEqual(update_logs[0].request_payload["date_range_overrides"][0]["start_date"], "2026-03-28")
        self.assertEqual(update_logs[0].result_payload["windows"][1]["shift_label"], "night")
        self.assertEqual(update_logs[0].result_payload["date_overrides"][0]["windows"][0]["shift_label"], "sre")
        self.assertEqual(update_logs[0].result_payload["date_range_overrides"][0]["windows"][0]["shift_label"], "release")
        self.assertEqual(len(clear_logs), 1)
        self.assertEqual(clear_logs[0].request_id, "req-monitor-shift-schedule-2")
        self.assertTrue(clear_logs[0].result_payload["cleared"])

    def test_get_replay_worker_status_escalates_to_critical_at_failure_threshold(self) -> None:
        service = build_app_service(
            replay_worker_alert_warning_consecutive_failures=1,
            replay_worker_alert_critical_consecutive_failures=2,
        )
        self.addCleanup(service.close)

        service.repository.record_replay_worker_heartbeat(
            workspace_id="ops-ws-1",
            status="retrying",
            iteration=9,
            attempted_count=0,
            dispatched_count=0,
            failed_count=2,
            skipped_count=0,
            idle_polls=0,
            consecutive_failures=2,
            remaining_queued_count=2,
            error_message=None,
            emitted_at=datetime(2026, 3, 27, 10, 5, tzinfo=UTC),
        )

        status = service.get_replay_worker_status(workspace_id="ops-ws-1", history_limit=5)

        self.assertIsNotNone(status.alert)
        assert status.alert is not None
        self.assertEqual(status.alert.level, "critical")
        self.assertIn("threshold", status.alert.headline.lower())
        self.assertIn("critical threshold of 2", status.alert.detail)

    def test_get_replay_worker_status_uses_workspace_override_threshold(self) -> None:
        service = build_app_service(
            replay_worker_alert_warning_consecutive_failures=1,
            replay_worker_alert_critical_consecutive_failures=5,
        )
        self.addCleanup(service.close)
        service.update_replay_worker_alert_policy(
            "ops-ws-1",
            {
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 2,
            },
        )

        service.repository.record_replay_worker_heartbeat(
            workspace_id="ops-ws-1",
            status="retrying",
            iteration=11,
            attempted_count=0,
            dispatched_count=0,
            failed_count=2,
            skipped_count=0,
            idle_polls=0,
            consecutive_failures=2,
            remaining_queued_count=1,
            error_message=None,
            emitted_at=datetime(2026, 3, 27, 10, 8, tzinfo=UTC),
        )

        status = service.get_replay_worker_status(workspace_id="ops-ws-1", history_limit=5)

        self.assertIsNotNone(status.alert)
        assert status.alert is not None
        self.assertEqual(status.alert.level, "critical")
        self.assertIsNotNone(status.policy)
        assert status.policy is not None
        self.assertEqual(status.policy.source, "workspace_override")
        self.assertEqual(status.policy.critical_consecutive_failures, 2)

    def test_get_approval_task_returns_linked_task(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        approval_task = service.get_approval_task("approval-task-1")

        self.assertEqual(approval_task.incident_id, "incident-1")
        self.assertEqual(approval_task.recommendation_id, "recommendation-1")
        self.assertEqual(approval_task.status, "pending")

    def test_decide_approval_task_orchestrates_recommendation_execution_and_comms_publish(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-1"
            draft_row.updated_at = now

        response = service.decide_approval_task(
            "approval-task-1",
            approval_decision_command(
                decision="approve",
                execute_recommendation=True,
                publish_linked_drafts=True,
                expected_fact_set_version=1,
            ),
            idempotency_key="approval-orchestrate-1",
        )
        workspace = service.get_incident_workspace("incident-1")
        pending = service.runtime_stores.outbox_store.list_pending()

        self.assertEqual(response.approval_task.status, "approved")
        self.assertIsNotNone(response.recommendation)
        self.assertEqual(response.recommendation.status, "executed")
        self.assertEqual(response.recommendation.approval_status, "approved")
        self.assertEqual(len(response.published_drafts), 1)
        self.assertEqual(response.published_drafts[0].draft_id, "draft-1")
        self.assertEqual(response.published_drafts[0].status, "published")
        self.assertEqual(workspace.recommendations[0].status, "executed")
        self.assertEqual(workspace.approval_tasks[0].status, "approved")
        self.assertEqual(workspace.comms_drafts[0].status, "published")
        self.assertTrue(
            any(
                item.event.event_name == "opsgraph.approval.updated"
                and item.event.payload.get("approval_task_id") == "approval-task-1"
                for item in pending
            )
        )
        self.assertTrue(
            any(
                item.event.event_name == "opsgraph.comms.updated"
                and item.event.payload.get("draft_id") == "draft-1"
                for item in pending
            )
        )

    def test_decide_approval_task_supports_comms_only_approval_and_idempotency(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-draft-pending",
                    incident_id="incident-1",
                    recommendation_id=None,
                    status="pending",
                    comment=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-draft-pending"
            draft_row.updated_at = now

        first = service.decide_approval_task(
            "approval-task-draft-pending",
            approval_decision_command(
                decision="approve",
                linked_draft_ids=["draft-1"],
                expected_fact_set_version=1,
            ),
            idempotency_key="approval-orchestrate-2",
        )
        second = service.decide_approval_task(
            "approval-task-draft-pending",
            approval_decision_command(
                decision="approve",
                linked_draft_ids=["draft-1"],
                expected_fact_set_version=1,
            ),
            idempotency_key="approval-orchestrate-2",
        )

        self.assertEqual(first.approval_task.approval_task_id, second.approval_task.approval_task_id)
        self.assertEqual(first.approval_task.status, "approved")
        self.assertIsNone(first.recommendation)
        self.assertEqual(len(first.published_drafts), 1)
        self.assertEqual(first.published_drafts[0].status, "published")

    def test_decide_approval_task_uses_remote_publish_for_linked_drafts(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        class _RemotePublishClient:
            def __init__(self) -> None:
                self.post_calls: list[dict[str, object]] = []

            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                raise RuntimeError("GET not expected")

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
                follow_redirects: bool,
                timeout: float,
            ):
                self.post_calls.append({"url": url, "json": dict(json)})

                class _Response:
                    status_code = 200
                    text = ""
                    url = "https://publisher.example.test/messages/internal_slack"

                    @staticmethod
                    def json():
                        return {
                            "published_message_ref": "slack-msg-approval-1",
                            "delivery_state": "published",
                        }

                    @staticmethod
                    def raise_for_status() -> None:
                        return None

                return _Response()

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-1"
            draft_row.updated_at = now

        service.repository.remote_tool_resolver = service.repository.remote_tool_resolver.__class__(
            http_client=_RemotePublishClient()
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://publisher.example.test/messages/{channel_type}",
            },
            clear=False,
        ):
            response = service.decide_approval_task(
                "approval-task-1",
                approval_decision_command(
                    decision="approve",
                    publish_linked_drafts=True,
                    expected_fact_set_version=1,
                ),
                idempotency_key="approval-orchestrate-remote-1",
            )

        self.assertEqual(len(response.published_drafts), 1)
        self.assertEqual(response.published_drafts[0].published_message_ref, "slack-msg-approval-1")
        self.assertEqual(response.published_drafts[0].delivery_state, "published")
        self.assertEqual(response.published_drafts[0].delivery_confirmed, True)

    def test_decide_approval_task_validates_orchestration_inputs(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "APPROVAL_DECISION_INVALID"):
            service.decide_approval_task(
                "approval-task-1",
                approval_decision_command(decision="reject", execute_recommendation=True),
            )

        with self.assertRaisesRegex(ValueError, "APPROVAL_PUBLISH_FACT_SET_REQUIRED"):
            service.decide_approval_task(
                "approval-task-1",
                approval_decision_command(decision="approve", publish_linked_drafts=True),
            )

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-no-rec",
                    incident_id="incident-1",
                    recommendation_id=None,
                    status="pending",
                    comment=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        with self.assertRaisesRegex(ValueError, "APPROVAL_EXECUTION_REQUIRES_RECOMMENDATION"):
            service.decide_approval_task(
                "approval-task-no-rec",
                approval_decision_command(decision="approve", execute_recommendation=True),
            )

        with self.assertRaisesRegex(ValueError, "APPROVAL_DRAFT_SELECTION_INVALID"):
            service.decide_approval_task(
                "approval-task-no-rec",
                approval_decision_command(
                    decision="approve",
                    linked_draft_ids=["draft-missing"],
                    expected_fact_set_version=1,
                ),
            )

    def test_decide_approval_task_rejects_idempotency_conflict_for_different_payload(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.decide_approval_task(
            "approval-task-1",
            approval_decision_command(decision="approve"),
            idempotency_key="approval-conflict-1",
        )

        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            service.decide_approval_task(
                "approval-task-1",
                approval_decision_command(decision="reject", comment="Different payload."),
                idempotency_key="approval-conflict-1",
            )

    def test_decide_approval_task_conflicts_after_terminal_state(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.decide_approval_task(
            "approval-task-1",
            approval_decision_command(decision="approve"),
        )

        with self.assertRaisesRegex(ValueError, "APPROVAL_STATUS_CONFLICT"):
            service.decide_approval_task(
                "approval-task-1",
                approval_decision_command(decision="approve"),
            )

    def test_decide_approval_task_rejects_stale_fact_set_when_publishing(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-draft-stale",
                    incident_id="incident-1",
                    recommendation_id=None,
                    status="pending",
                    comment=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-draft-stale"
            draft_row.updated_at = now

        service.add_fact("incident-1", fact_create_command())

        with self.assertRaisesRegex(ValueError, "COMM_DRAFT_STALE_FACT_SET"):
            service.decide_approval_task(
                "approval-task-draft-stale",
                approval_decision_command(
                    decision="approve",
                    publish_linked_drafts=True,
                    expected_fact_set_version=1,
                ),
            )

    def test_decide_approval_task_rejects_already_published_linked_draft(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-draft-published",
                    incident_id="incident-1",
                    recommendation_id=None,
                    status="pending",
                    comment=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-draft-published"
            draft_row.status = "published"
            draft_row.published_message_ref = "internal_slack-msg-existing"
            draft_row.updated_at = now

        with self.assertRaisesRegex(ValueError, "COMM_DRAFT_ALREADY_PUBLISHED"):
            service.decide_approval_task(
                "approval-task-draft-published",
                approval_decision_command(
                    decision="approve",
                    publish_linked_drafts=True,
                    expected_fact_set_version=1,
                ),
            )

    def test_list_comms_supports_channel_and_status_filters(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        by_channel = service.list_comms("incident-1", channel="internal_slack")
        by_status = service.list_comms("incident-1", status="draft")
        missing = service.list_comms("incident-1", channel="email")

        self.assertEqual(len(by_channel), 1)
        self.assertEqual(by_channel[0].draft_id, "draft-1")
        self.assertEqual(by_channel[0].approval_task_id, None)
        self.assertEqual(len(by_status), 1)
        self.assertEqual(missing, [])

    def test_recommendation_execution_requires_approval_and_conflicts_after_terminal(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "APPROVAL_REQUIRED"):
            service.decide_recommendation(
                "incident-1",
                "recommendation-1",
                recommendation_decision_command(decision="mark_executed"),
            )

        approved = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(),
        )
        executed = service.decide_recommendation(
            "incident-1",
            "recommendation-1",
            recommendation_decision_command(decision="mark_executed"),
        )

        self.assertEqual(approved.status, "approved")
        self.assertEqual(executed.status, "executed")
        with self.assertRaisesRegex(ValueError, "RECOMMENDATION_STATUS_CONFLICT"):
            service.decide_recommendation(
                "incident-1",
                "recommendation-1",
                recommendation_decision_command(decision="reject"),
            )

    def test_recommendation_rejects_mismatched_approval_task(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "APPROVAL_REQUIRED"):
            service.decide_recommendation(
                "incident-1",
                "recommendation-1",
                recommendation_decision_command(approval_task_id="approval-task-wrong"),
            )

    def test_publish_comms_rejects_stale_fact_set(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.add_fact("incident-1", fact_create_command())

        with self.assertRaisesRegex(ValueError, "COMM_DRAFT_STALE_FACT_SET"):
            service.publish_comms(
                "incident-1",
                "draft-1",
                comms_publish_command(expected_fact_set_version=1),
            )

    def test_publish_comms_requires_matching_approved_task(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        now = service.repository._utcnow_naive()
        with service.repository.session_factory.begin() as session:
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-draft-1",
                    incident_id="incident-1",
                    recommendation_id=None,
                    status="approved",
                    comment="Approved for publish.",
                    created_at=now,
                    updated_at=now,
                )
            )
            draft_row = session.get(CommsDraftRow, "draft-1")
            self.assertIsNotNone(draft_row)
            draft_row.approval_task_id = "approval-task-draft-1"
            draft_row.updated_at = now

        with self.assertRaisesRegex(ValueError, "APPROVAL_REQUIRED"):
            service.publish_comms(
                "incident-1",
                "draft-1",
                comms_publish_command(
                    expected_fact_set_version=1,
                    approval_task_id="approval-task-wrong",
                ),
            )

        published = service.publish_comms(
            "incident-1",
            "draft-1",
            comms_publish_command(
                expected_fact_set_version=1,
                approval_task_id="approval-task-draft-1",
            ),
        )

        self.assertEqual(published.status, "published")

    def test_execute_replay_run_triggers_workflow_and_marks_completed(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        replay = service.start_replay_run(replay_run_command())
        executed = service.execute_replay_run(replay.replay_run_id)
        state = service.get_workflow_state(executed.workflow_run_id)

        self.assertEqual(executed.status, "completed")
        self.assertIsNotNone(executed.workflow_run_id)
        self.assertEqual(executed.current_state, "resolve")
        self.assertEqual(state.current_state, "resolve")

    def test_execute_replay_case_run_marks_completed(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-retro-replay-case"))
        with service.repository.session_factory() as session:
            postmortem_row = session.scalars(
                select(PostmortemRow).where(PostmortemRow.incident_id == "incident-1")
            ).first()
            self.assertIsNotNone(postmortem_row)
            replay_case_id = postmortem_row.replay_case_id

        replay = service.start_replay_run(replay_case_run_command(replay_case_id=replay_case_id))
        executed = service.execute_replay_run(replay.replay_run_id)
        state = service.get_workflow_state(executed.workflow_run_id)

        self.assertEqual(replay.replay_case_id, replay_case_id)
        self.assertEqual(executed.status, "completed")
        self.assertEqual(executed.current_state, "resolve")
        self.assertEqual(state.current_state, "resolve")

    def test_capture_baseline_and_evaluate_replay_report(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_run_command())
        executed = service.execute_replay_run(replay.replay_run_id)
        baseline_state = service.get_workflow_state(baseline.workflow_run_id)
        with service.runtime_stores.state_store.session_factory.begin() as session:
            state_row = session.get(WorkflowStateRow, executed.workflow_run_id)
            self.assertIsNotNone(state_row)
            state_payload = dict(state_row.state_payload)
            for key in (
                "service_id",
                "incident_status",
                "current_fact_set_version",
                "hypothesis_payloads",
                "recommendation_payloads",
                "comms_payloads",
            ):
                state_payload[key] = baseline_state.raw_state.get(key)
            state_row.state_payload = state_payload
        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
        )
        baselines = service.list_replay_baselines("ops-ws-1", "incident-1")
        reports = service.list_replay_evaluations("ops-ws-1", replay_run_id=replay.replay_run_id)

        self.assertEqual(baseline.final_state, "resolve")
        self.assertGreaterEqual(len(baseline.node_summaries), 1)
        self.assertEqual(executed.status, "completed")
        self.assertEqual(report.status, "matched")
        self.assertGreater(report.score, 0.9)
        self.assertGreaterEqual(len(report.node_diffs), 1)
        self.assertTrue(all(item.matched for item in report.node_diffs))
        self.assertEqual(report.matched_node_count, len(report.node_diffs))
        self.assertEqual(report.mismatched_node_count, 0)
        self.assertEqual(report.node_match_rate, 1.0)
        self.assertEqual(report.bundle_mismatch_count, 0)
        self.assertEqual(report.version_mismatch_count, 0)
        self.assertEqual(report.summary_mismatch_count, 0)
        self.assertEqual(report.missing_baseline_node_count, 0)
        self.assertEqual(report.missing_replay_node_count, 0)
        self.assertEqual(report.state_mismatch_count, 0)
        self.assertEqual(report.checkpoint_mismatch_count, 0)
        self.assertGreaterEqual(report.latency_improvement_count, 0)
        self.assertGreaterEqual(report.latency_regression_total_ms, 0)
        self.assertIsNotNone(report.avg_latency_delta_ms)
        self.assertEqual(report.semantic_mismatch_count, 0)
        self.assertGreaterEqual(report.semantic_check_count, 6)
        self.assertEqual(report.service_id_mismatch_count, 0)
        self.assertEqual(report.incident_status_mismatch_count, 0)
        self.assertEqual(report.fact_set_version_mismatch_count, 0)
        self.assertEqual(report.top_hypothesis_hit_rate, 1.0)
        self.assertEqual(report.recommendation_match_rate, 1.0)
        self.assertEqual(report.comms_match_rate, 1.0)
        self.assertTrue(all(item.matched for item in report.semantic_checks))
        self.assertIsNotNone(report.report_artifact_path)
        self.assertIsNotNone(report.markdown_report_path)
        self.assertIsNotNone(report.csv_report_path)
        self.assertTrue(Path(report.report_artifact_path).exists())
        self.assertTrue(Path(report.markdown_report_path).exists())
        self.assertTrue(Path(report.csv_report_path).exists())
        report_payload = json.loads(Path(report.report_artifact_path).read_text(encoding="utf-8"))
        self.assertEqual(report_payload["report"]["status"], "matched")
        self.assertEqual(report_payload["report"]["matched_node_count"], len(report.node_diffs))
        self.assertEqual(report_payload["report"]["semantic_mismatch_count"], 0)
        self.assertEqual(report_payload["report"]["top_hypothesis_hit_rate"], 1.0)
        self.assertEqual(report_payload["report"]["csv_report_path"], report.csv_report_path)
        csv_lines = Path(report.csv_report_path).read_text(encoding="utf-8").splitlines()
        self.assertTrue(csv_lines[0].startswith("checkpoint_seq,matched"))
        self.assertEqual(len(csv_lines), len(report.node_diffs) + 1)
        self.assertTrue(any(item.baseline_id == baseline.baseline_id for item in baselines))
        self.assertTrue(any(item.report_id == report.report_id for item in reports))

    def test_evaluate_replay_reports_richer_mismatch_metrics_and_csv_artifact(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_run_command())
        executed = service.execute_replay_run(replay.replay_run_id)
        expected_latency_regression = 0

        with service.runtime_stores.replay_store.session_factory.begin() as session:
            replay_rows = session.scalars(
                select(ReplayRecordRow)
                .where(ReplayRecordRow.workflow_run_id == executed.workflow_run_id)
                .order_by(ReplayRecordRow.checkpoint_seq.asc())
            ).all()
            self.assertGreaterEqual(len(replay_rows), 1)
            replay_rows[0].bundle_version = "2026-03-99.9"
            replay_rows[0].output_summary = "Injected summary mismatch for replay regression coverage."
            latency_row = replay_rows[1] if len(replay_rows) > 1 else replay_rows[0]
            latency_row.recorded_at = latency_row.recorded_at + timedelta(milliseconds=250)
            expected_latency_regression = 1 if len(replay_rows) > 1 else 0
            if len(replay_rows) > 1:
                session.delete(replay_rows[-1])
            state_row = session.get(WorkflowStateRow, executed.workflow_run_id)
            self.assertIsNotNone(state_row)
            state_row.checkpoint_seq = state_row.checkpoint_seq + 1
            state_payload = dict(state_row.state_payload)
            state_payload["current_state"] = "mitigate"
            state_payload["checkpoint_seq"] = int(state_payload.get("checkpoint_seq", state_row.checkpoint_seq - 1)) + 1
            state_payload["service_id"] = "billing-api"
            state_payload["incident_status"] = "responding"
            state_payload["hypothesis_payloads"] = [
                {
                    "hypothesis_id": "hypothesis-replay-regression-1",
                    "title": "Cache node saturation",
                    "confidence": 0.51,
                    "rank": 1,
                    "evidence_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                    "verification_steps": [],
                }
            ]
            state_payload["recommendation_payloads"] = [
                {
                    "recommendation_id": "recommendation-replay-regression-1",
                    "title": "Drain cache traffic",
                    "risk_level": "medium",
                    "requires_approval": False,
                    "instructions_markdown": "Shift traffic away from the hot cache node.",
                    "evidence_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                    "approval_task_id": None,
                }
            ]
            state_payload["comms_payloads"] = [
                {
                    "draft_id": "draft-replay-regression-1",
                    "channel_type": "external_status_page",
                    "fact_set_version": 99,
                    "body_markdown": "Unplanned maintenance in progress for the cache tier.",
                    "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                }
            ]
            state_row.state_payload = state_payload

        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
        )

        self.assertEqual(report.status, "mismatched")
        self.assertGreater(report.mismatch_count, 0)
        self.assertLess(report.score, 1.0)
        self.assertGreaterEqual(report.mismatched_node_count, 1)
        self.assertLess(report.node_match_rate, 1.0)
        self.assertGreaterEqual(report.version_mismatch_count, 1)
        self.assertGreaterEqual(report.summary_mismatch_count, 1)
        self.assertGreaterEqual(report.missing_replay_node_count, 1)
        self.assertEqual(report.state_mismatch_count, 1)
        self.assertEqual(report.checkpoint_mismatch_count, 1)
        self.assertGreaterEqual(report.latency_regression_count, expected_latency_regression)
        self.assertGreaterEqual(report.semantic_mismatch_count, 5)
        self.assertEqual(report.service_id_mismatch_count, 1)
        self.assertEqual(report.incident_status_mismatch_count, 1)
        self.assertEqual(report.top_hypothesis_hit_rate, 0.0)
        self.assertEqual(report.recommendation_match_rate, 0.0)
        self.assertEqual(report.comms_match_rate, 0.0)
        self.assertTrue(any(not item.matched for item in report.semantic_checks))
        if expected_latency_regression:
            self.assertGreater(report.latency_regression_total_ms, 0)
        self.assertIsNotNone(report.avg_latency_delta_ms)
        self.assertIsNotNone(report.csv_report_path)
        self.assertTrue(Path(report.csv_report_path).exists())

        report_payload = json.loads(Path(report.report_artifact_path).read_text(encoding="utf-8"))
        self.assertEqual(report_payload["report"]["status"], "mismatched")
        self.assertEqual(report_payload["report"]["version_mismatch_count"], report.version_mismatch_count)
        self.assertEqual(report_payload["report"]["state_mismatch_count"], 1)
        self.assertEqual(report_payload["report"]["semantic_mismatch_count"], report.semantic_mismatch_count)
        self.assertEqual(report_payload["artifacts"]["csv_report_path"], report.csv_report_path)
        csv_payload = Path(report.csv_report_path).read_text(encoding="utf-8")
        self.assertIn("Injected summary mismatch for replay regression coverage.", csv_payload)

    def test_replay_quality_summary_reports_baseline_coverage_and_pass_rates(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-quality-summary-1"))
        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_run_command())
        executed = service.execute_replay_run(replay.replay_run_id)
        baseline_state = service.get_workflow_state(baseline.workflow_run_id)
        with service.runtime_stores.state_store.session_factory.begin() as session:
            state_row = session.get(WorkflowStateRow, executed.workflow_run_id)
            self.assertIsNotNone(state_row)
            state_payload = dict(state_row.state_payload)
            for key in (
                "service_id",
                "incident_status",
                "current_fact_set_version",
                "hypothesis_payloads",
                "recommendation_payloads",
                "comms_payloads",
            ):
                state_payload[key] = baseline_state.raw_state.get(key)
            state_row.state_payload = state_payload
        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
        )

        summary = service.get_replay_quality_summary("ops-ws-1")
        incident_summary = service.get_replay_quality_summary("ops-ws-1", "incident-1")

        self.assertEqual(summary.incident_count, 1)
        self.assertEqual(summary.replay_case_count, 1)
        self.assertEqual(summary.replay_case_expected_output_count, 1)
        self.assertEqual(summary.replay_case_expected_output_coverage_rate, 1.0)
        self.assertEqual(summary.baseline_count, 1)
        self.assertEqual(summary.baseline_incident_coverage_count, 1)
        self.assertEqual(summary.baseline_coverage_rate, 1.0)
        self.assertEqual(summary.evaluation_count, 1)
        self.assertEqual(summary.matched_evaluation_count, 1)
        self.assertEqual(summary.mismatched_evaluation_count, 0)
        self.assertEqual(summary.replay_pass_rate, 1.0)
        self.assertEqual(summary.avg_replay_score, report.score)
        self.assertEqual(summary.semantic_evaluation_count, 1)
        self.assertEqual(summary.avg_semantic_match_rate, report.semantic_match_rate)
        self.assertEqual(summary.avg_top_hypothesis_hit_rate, 1.0)
        self.assertEqual(summary.avg_recommendation_match_rate, 1.0)
        self.assertEqual(summary.avg_comms_match_rate, 1.0)
        self.assertEqual(summary.latest_report_id, report.report_id)
        self.assertEqual(incident_summary.incident_id, "incident-1")
        self.assertEqual(incident_summary.evaluation_count, 1)

    def test_evaluate_replay_requires_executed_run(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_run_command())

        with self.assertRaisesRegex(ValueError, "REPLAY_RUN_NOT_EXECUTED"):
            service.evaluate_replay_run(
                replay.replay_run_id,
                replay_evaluation_command(baseline_id=baseline.baseline_id),
            )

    def test_respond_to_incident_and_load_state(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        result = service.respond_to_incident(incident_response_command(workflow_run_id="opsgraph-service-1"))
        state = service.get_workflow_state("opsgraph-service-1")

        self.assertEqual(result.current_state, "resolve")
        self.assertEqual(state.current_state, "resolve")
        self.assertEqual(state.workflow_type, "opsgraph_incident")
        self.assertEqual(state.raw_state["service_id"], "checkout-api")
        self.assertTrue(state.raw_state["recommendation_ids"])
        self.assertTrue(state.raw_state["comms_draft_ids"])
        self.assertTrue(state.raw_state["top_hypothesis_ids"])

    def test_respond_to_incident_emits_incident_updated_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.respond_to_incident(incident_response_command(workflow_run_id="opsgraph-event-respond-1"))
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.workflow_run_id == "opsgraph-event-respond-1"
            and item.event.event_name == "opsgraph.incident.updated"
        ]

        self.assertTrue(any(event.payload.get("current_state") == "resolve" for event in matching))

    def test_respond_to_incident_emits_generated_approval_requested_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        ingest = service.ingest_alert(
            alert_ingest_command(
                correlation_key="payments-api:latency-surge",
                summary="Payments API latency surge",
                source="grafana",
            )
        )
        command = service.repository.get_incident_execution_seed(ingest.incident_id) | {
            "workflow_run_id": "opsgraph-generated-events-1"
        }
        service.respond_to_incident(command)
        pending = service.runtime_stores.outbox_store.list_pending()

        approval_events = [
            item.event
            for item in pending
            if item.event.workflow_run_id == "opsgraph-generated-events-1"
            and item.event.event_name == "opsgraph.approval.requested"
        ]
        self.assertEqual(len(approval_events), 1)
        self.assertEqual(approval_events[0].payload.get("subject_type"), "runbook_recommendation")

    def test_build_retrospective_from_domain_command(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        resolved = service.resolve_incident("incident-1", resolve_incident_command())
        result = service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-retro-1"))
        postmortem = service.get_postmortem("incident-1")
        workspace = service.get_incident_workspace("incident-1")
        with service.repository.session_factory() as session:
            postmortem_row = session.scalars(
                select(PostmortemRow).where(PostmortemRow.incident_id == "incident-1")
            ).first()
            self.assertIsNotNone(postmortem_row)
            replay_case_row = session.get(ReplayCaseRow, postmortem_row.replay_case_id)
            artifact_row = session.get(ArtifactBlobRow, postmortem.artifact_id)
            self.assertIsNotNone(artifact_row)
            artifact_payload = json.loads(artifact_row.content_text)

        self.assertEqual(resolved.incident_status, "resolved")
        self.assertEqual(result.workflow_name, "opsgraph_retrospective")
        self.assertEqual(result.current_state, "retrospective_completed")
        self.assertEqual(postmortem.status, "draft")
        self.assertIsNotNone(postmortem.artifact_id)
        self.assertEqual(workspace.incident.incident_status, "closed")
        self.assertIsNotNone(postmortem.replay_case_id)
        self.assertIsNotNone(postmortem_row.replay_case_id)
        self.assertIsNotNone(replay_case_row)
        self.assertEqual(replay_case_row.incident_id, "incident-1")
        self.assertEqual(replay_case_row.input_snapshot_payload["incident_id"], "incident-1")
        self.assertIsInstance(replay_case_row.expected_output_payload, dict)
        self.assertEqual(replay_case_row.expected_output_payload["incident"]["incident_id"], "incident-1")
        self.assertEqual(
            replay_case_row.expected_output_payload["workflow"]["current_state"],
            "retrospective_completed",
        )
        self.assertTrue(replay_case_row.expected_output_payload["comms_drafts"])
        self.assertEqual(artifact_payload["incident_key"], "INC-2026-0001")
        self.assertIn("timeline", artifact_payload)
        self.assertIn("postmortem_markdown", artifact_payload)
        self.assertTrue(artifact_payload["follow_up_actions"])
        self.assertTrue(artifact_payload["replay_capture_hints"])
        retrospective_state = service.get_workflow_state("opsgraph-retro-1")
        self.assertEqual(retrospective_state.raw_state["postmortem_id"], postmortem.postmortem_id)
        self.assertEqual(retrospective_state.raw_state["postmortem_status"], "draft")
        self.assertEqual(retrospective_state.raw_state["incident_status"], "closed")

    def test_build_retrospective_emits_postmortem_ready_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-event-retro-1"))
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.workflow_run_id == "opsgraph-event-retro-1"
            and item.event.event_name == "opsgraph.postmortem.ready"
        ]

        self.assertTrue(any(event.payload.get("postmortem_status") == "draft" for event in matching))

    def test_finalize_postmortem_marks_final_and_emits_update_event(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-event-retro-finalize"))
        finalized = service.finalize_postmortem(
            "incident-1",
            postmortem_finalize_command(finalized_by_user_id="ic-user-1"),
            idempotency_key="postmortem-finalize-1",
        )
        pending = service.runtime_stores.outbox_store.list_pending()
        matching = [
            item.event
            for item in pending
            if item.event.event_name == "opsgraph.postmortem.updated"
            and item.event.payload.get("postmortem_id") == finalized.postmortem_id
        ]

        self.assertEqual(finalized.status, "final")
        self.assertEqual(finalized.finalized_by_user_id, "ic-user-1")
        self.assertIsNotNone(finalized.finalized_at)
        self.assertTrue(any(event.payload.get("postmortem_status") == "final" for event in matching))

        with service.repository.session_factory() as session:
            artifact_row = session.get(ArtifactBlobRow, finalized.artifact_id)
            self.assertIsNotNone(artifact_row)
            payload = json.loads(artifact_row.content_text)

        self.assertEqual(payload["status"], "final")
        self.assertEqual(payload["finalized_by_user_id"], "ic-user-1")

    def test_list_postmortems_supports_workspace_incident_and_status_filters(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        first = service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-postmortem-list-1"))
        service.finalize_postmortem("incident-1", postmortem_finalize_command())
        ingest = service.ingest_alert(
            alert_ingest_command(
                correlation_key="catalog-api:error-spike",
                summary="Catalog API error spike",
                source="grafana",
            )
        )
        created_fact = service.add_fact(ingest.incident_id, fact_create_command())
        service.resolve_incident(
            ingest.incident_id,
            resolve_incident_command() | {"root_cause_fact_ids": [created_fact.fact_id]},
        )
        service.build_retrospective(
            retrospective_command(workflow_run_id="opsgraph-postmortem-list-2")
            | {
                "incident_id": ingest.incident_id,
                "current_fact_set_version": 2,
                "confirmed_fact_refs": [{"kind": "incident_fact", "id": created_fact.fact_id}],
                "timeline_refs": [],
                "resolution_summary": "Scaled catalog workers and drained the queue.",
            }
        )

        all_items = service.list_postmortems("ops-ws-1")
        incident_one = service.list_postmortems("ops-ws-1", incident_id="incident-1")
        drafts = service.list_postmortems("ops-ws-1", status="draft")
        finals = service.list_postmortems("ops-ws-1", status="final")

        self.assertEqual(first.current_state, "retrospective_completed")
        self.assertEqual(len(all_items), 2)
        self.assertEqual(incident_one[0].incident_id, "incident-1")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(len(finals), 1)

    def test_list_and_get_replay_cases_from_postmortem_snapshot(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-retro-case-read"))
        postmortem = service.get_postmortem("incident-1")

        replay_cases = service.list_replay_cases("ops-ws-1", "incident-1")
        replay_case = service.get_replay_case(postmortem.replay_case_id)

        self.assertEqual(len(replay_cases), 1)
        self.assertEqual(replay_cases[0].replay_case_id, postmortem.replay_case_id)
        self.assertEqual(replay_case.incident_id, "incident-1")
        self.assertEqual(replay_case.case_name, "INC-2026-0001 retrospective replay")
        self.assertEqual(replay_case.input_snapshot["incident_id"], "incident-1")
        self.assertEqual(replay_case.input_snapshot["target_channels"], ["internal_slack"])
        self.assertIsNotNone(replay_case.expected_output)
        self.assertEqual(replay_case.expected_output["incident"]["incident_key"], "INC-2026-0001")
        self.assertTrue(replay_case.expected_output["recommendations"])

    def test_list_replays_can_filter_by_replay_case_id(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-retro-case-filter"))
        postmortem = service.get_postmortem("incident-1")

        replay = service.start_replay_run(replay_case_run_command(replay_case_id=postmortem.replay_case_id))
        filtered = service.list_replays("ops-ws-1", replay_case_id=postmortem.replay_case_id)
        unrelated = service.list_replays("ops-ws-1", replay_case_id="replay-case-missing")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].replay_run_id, replay.replay_run_id)
        self.assertEqual(filtered[0].replay_case_id, postmortem.replay_case_id)
        self.assertEqual(unrelated, [])

    def test_list_replays_can_filter_by_status(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        queued = service.start_replay_run(replay_run_command(model_bundle_version="opsgraph-v1.3"))
        completed = service.start_replay_run(replay_run_command(model_bundle_version="opsgraph-v1.4"))
        service.update_replay_status(completed.replay_run_id, replay_status_command())

        queued_runs = service.list_replays("ops-ws-1", status="queued")
        completed_runs = service.list_replays("ops-ws-1", status="completed")

        self.assertTrue(any(item.replay_run_id == queued.replay_run_id for item in queued_runs))
        self.assertFalse(any(item.replay_run_id == queued.replay_run_id for item in completed_runs))
        self.assertTrue(any(item.replay_run_id == completed.replay_run_id for item in completed_runs))

    def test_process_queued_replays_executes_oldest_queued_runs(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        auth_context = self._issue_auth_context(
            service,
            email="admin@example.com",
            required_role="product_admin",
        )
        newer = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-batch-v2"),
            idempotency_key="replay-batch-newer",
        )
        older = service.start_replay_run(
            replay_run_command(model_bundle_version="opsgraph-batch-v1"),
            idempotency_key="replay-batch-older",
        )
        with service.repository.session_factory.begin() as session:
            older_row = session.get(ReplayRunRow, older.replay_run_id)
            newer_row = session.get(ReplayRunRow, newer.replay_run_id)
            self.assertIsNotNone(older_row)
            self.assertIsNotNone(newer_row)
            older_row.created_at = older_row.created_at - timedelta(minutes=5)

        processed = service.process_queued_replays(
            "ops-ws-1",
            limit=1,
            auth_context=auth_context,
            request_id="req-replay-process-1",
        )
        queued_runs = service.list_replays("ops-ws-1", status="queued")
        execute_logs = service.list_audit_logs(
            "incident-1",
            action_type="replay.execute",
            actor_user_id="user-admin-1",
        )

        self.assertEqual(processed.workspace_id, "ops-ws-1")
        self.assertEqual(processed.queued_count, 2)
        self.assertEqual(processed.processed_count, 1)
        self.assertEqual(processed.completed_count, 1)
        self.assertEqual(processed.failed_count, 0)
        self.assertEqual(processed.skipped_count, 0)
        self.assertEqual(processed.remaining_queued_count, 1)
        self.assertEqual(len(processed.items), 1)
        self.assertEqual(processed.items[0].replay_run_id, older.replay_run_id)
        self.assertEqual(processed.items[0].status, "completed")
        self.assertEqual(len(queued_runs), 1)
        self.assertEqual(queued_runs[0].replay_run_id, newer.replay_run_id)
        self.assertEqual(len(execute_logs), 1)
        self.assertEqual(execute_logs[0].request_id, "req-replay-process-1")
        self.assertEqual(execute_logs[0].subject_id, older.replay_run_id)

    def test_process_queued_replays_rejects_invalid_batch_limit(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "INVALID_REPLAY_BATCH_LIMIT"):
            service.process_queued_replays("ops-ws-1", limit=0)

    def test_replay_status_rejects_transition_from_terminal_state(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        replay = service.start_replay_run(replay_run_command(model_bundle_version="opsgraph-v1.5"))
        service.update_replay_status(replay.replay_run_id, replay_status_command())

        with self.assertRaisesRegex(ValueError, "REPLAY_STATUS_CONFLICT"):
            service.update_replay_status(
                replay.replay_run_id,
                replay_status_command(status="running"),
            )

    def test_list_replay_reports_can_filter_by_replay_case_id(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        service.resolve_incident("incident-1", resolve_incident_command())
        service.build_retrospective(retrospective_command(workflow_run_id="opsgraph-retro-report-filter"))
        postmortem = service.get_postmortem("incident-1")

        baseline = service.capture_replay_baseline(replay_baseline_capture_command())
        replay = service.start_replay_run(replay_case_run_command(replay_case_id=postmortem.replay_case_id))
        service.execute_replay_run(replay.replay_run_id)
        report = service.evaluate_replay_run(
            replay.replay_run_id,
            replay_evaluation_command(baseline_id=baseline.baseline_id),
        )

        filtered = service.list_replay_evaluations("ops-ws-1", replay_case_id=postmortem.replay_case_id)
        unrelated = service.list_replay_evaluations("ops-ws-1", replay_case_id="replay-case-missing")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].report_id, report.report_id)
        self.assertEqual(unrelated, [])

    def test_resolve_requires_confirmed_root_cause_fact(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "ROOT_CAUSE_FACT_REQUIRED"):
            service.resolve_incident(
                "incident-1",
                {
                    "resolution_summary": "Rollback restored service.",
                    "root_cause_fact_ids": [],
                },
            )

        with self.assertRaisesRegex(ValueError, "ROOT_CAUSE_FACT_REQUIRED"):
            service.resolve_incident(
                "incident-1",
                {
                    "resolution_summary": "Rollback restored service.",
                    "root_cause_fact_ids": ["fact-missing"],
                },
            )

    def test_close_requires_resolved_incident_and_resolve_conflicts_after_terminal(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        with self.assertRaisesRegex(ValueError, "INCIDENT_NOT_RESOLVED"):
            service.close_incident("incident-1", close_incident_command())

        resolved = service.resolve_incident("incident-1", resolve_incident_command())
        self.assertEqual(resolved.incident_status, "resolved")

        with self.assertRaisesRegex(ValueError, "INCIDENT_ALREADY_RESOLVED"):
            service.resolve_incident("incident-1", resolve_incident_command())

        closed = service.close_incident("incident-1", close_incident_command())
        self.assertEqual(closed.incident_status, "closed")

    def test_sqlalchemy_repository_persists_incident_updates_across_service_instances(self) -> None:
        tmp_dir = create_repo_tempdir("opsgraph-db-")
        database_url = f"sqlite+pysqlite:///{(tmp_dir / 'opsgraph.db').resolve().as_posix()}"

        service_one = build_app_service(database_url=database_url)
        service_two = None
        try:
            ingest = service_one.ingest_alert(
                alert_ingest_command(
                    correlation_key="payments-api:latency-spike",
                    summary="Payments API latency spike",
                )
            )
            service_one.close()

            service_two = build_app_service(database_url=database_url)
            incidents = service_two.list_incidents("ops-ws-1")

            self.assertTrue(ingest.incident_created)
            self.assertTrue(any(item.incident_id == ingest.incident_id for item in incidents))
        finally:
            service_one.close()
            if service_two is not None:
                service_two.close()
            cleanup_repo_tempdir(tmp_dir)
