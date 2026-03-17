from __future__ import annotations

import unittest
from datetime import UTC, datetime

from agent_platform import AuditCycleWorkflowState, IncidentWorkflowState


class WorkflowStateTests(unittest.TestCase):
    def test_auditflow_state_serializes_to_langgraph_state(self) -> None:
        state = AuditCycleWorkflowState(
            workflow_run_id="wf-1",
            organization_id="org-1",
            workspace_id="ws-1",
            subject_id="cycle-1",
            trigger_type="api_command",
            run_config_version="2026-03-16.1",
            last_transition_at=datetime.now(UTC),
            audit_workspace_id="audit-ws-1",
            audit_cycle_id="cycle-1",
            cycle_status="ingesting",
            working_snapshot_version=3,
        )

        as_dict = state.to_langgraph_state()
        self.assertEqual(as_dict["workflow_type"], "auditflow_cycle")
        self.assertEqual(as_dict["subject_type"], "audit_cycle")
        self.assertEqual(as_dict["current_state"], "workspace_setup")
        self.assertEqual(as_dict["working_snapshot_version"], 3)

    def test_opsgraph_state_serializes_to_langgraph_state(self) -> None:
        state = IncidentWorkflowState(
            workflow_run_id="wf-2",
            organization_id="org-1",
            workspace_id="ws-2",
            subject_id="incident-1",
            trigger_type="webhook",
            run_config_version="2026-03-16.1",
            last_transition_at=datetime.now(UTC),
            ops_workspace_id="ops-ws-1",
            incident_id="incident-1",
            incident_status="investigating",
            severity="sev1",
            current_fact_set_version=2,
        )

        as_dict = state.to_langgraph_state()
        self.assertEqual(as_dict["workflow_type"], "opsgraph_incident")
        self.assertEqual(as_dict["subject_type"], "incident")
        self.assertEqual(as_dict["current_state"], "detect")
        self.assertEqual(as_dict["current_fact_set_version"], 2)


if __name__ == "__main__":
    unittest.main()
