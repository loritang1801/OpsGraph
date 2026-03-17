from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.repository import ApprovalTaskRow, CommsDraftRow, PostmortemRow, ReplayCaseRow
from opsgraph_app.sample_payloads import (
    alert_ingest_command,
    close_incident_command,
    comms_publish_command,
    fact_create_command,
    fact_retract_command,
    hypothesis_decision_command,
    incident_response_command,
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


class OpsGraphServiceTests(unittest.TestCase):
    def test_query_incident_workspace_and_alert_ingest(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        incidents = service.list_incidents("ops-ws-1")
        workspace = service.get_incident_workspace("incident-1")
        ingest = service.ingest_alert(alert_ingest_command())

        self.assertEqual(len(incidents), 1)
        self.assertEqual(workspace.incident.incident_key, "INC-2026-0001")
        self.assertEqual(ingest.incident_id, "incident-1")
        self.assertFalse(ingest.incident_created)

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
        self.assertEqual(severity.severity, "sev2")
        self.assertEqual(published.status, "published")
        self.assertEqual(replay.status, "queued")
        self.assertEqual(replay_updated.status, "completed")
        self.assertGreaterEqual(len(replays), 1)
        self.assertEqual(len(workspace.hypotheses), 1)
        self.assertEqual(len(workspace.approval_tasks), 1)
        self.assertEqual(workspace.approval_tasks[0].approval_task_id, "approval-task-1")
        self.assertEqual(workspace.recommendations[0].approval_task_id, "approval-task-1")

    def test_get_approval_task_returns_linked_task(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        approval_task = service.get_approval_task("approval-task-1")

        self.assertEqual(approval_task.incident_id, "incident-1")
        self.assertEqual(approval_task.recommendation_id, "recommendation-1")
        self.assertEqual(approval_task.status, "pending")

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
        self.assertEqual(report.bundle_mismatch_count, 0)
        self.assertEqual(report.summary_mismatch_count, 0)
        self.assertIsNotNone(report.report_artifact_path)
        self.assertIsNotNone(report.markdown_report_path)
        self.assertTrue(Path(report.report_artifact_path).exists())
        self.assertTrue(Path(report.markdown_report_path).exists())
        report_payload = json.loads(Path(report.report_artifact_path).read_text(encoding="utf-8"))
        self.assertEqual(report_payload["report"]["status"], "matched")
        self.assertEqual(report_payload["report"]["matched_node_count"], len(report.node_diffs))
        self.assertTrue(any(item.baseline_id == baseline.baseline_id for item in baselines))
        self.assertTrue(any(item.report_id == report.report_id for item in reports))

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

        self.assertEqual(resolved.incident_status, "resolved")
        self.assertEqual(result.workflow_name, "opsgraph_retrospective")
        self.assertEqual(result.current_state, "retrospective_completed")
        self.assertEqual(postmortem.status, "draft")
        self.assertEqual(workspace.incident.incident_status, "closed")
        self.assertIsNotNone(postmortem.replay_case_id)
        self.assertIsNotNone(postmortem_row.replay_case_id)
        self.assertIsNotNone(replay_case_row)
        self.assertEqual(replay_case_row.incident_id, "incident-1")
        self.assertEqual(replay_case_row.input_snapshot_payload["incident_id"], "incident-1")

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
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite+pysqlite:///{Path(tmp_dir) / 'opsgraph.db'}"

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
