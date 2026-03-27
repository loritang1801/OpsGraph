from __future__ import annotations

import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agent_platform import create_sqlalchemy_runtime_stores
from agent_platform.events import OutboxEvent
from agent_platform.checkpoints import ReplayRecord, WorkflowCheckpoint
from agent_platform.persistence import WorkflowStateRecord


class SqlAlchemyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.stores = create_sqlalchemy_runtime_stores(engine=engine)
        self.addCleanup(self.stores.dispose)

    def test_state_store_round_trip(self) -> None:
        record = WorkflowStateRecord(
            workflow_run_id="wf-state-1",
            workflow_type="auditflow_cycle",
            checkpoint_seq=2,
            state={"current_state": "mapping", "checkpoint_seq": 2},
            updated_at=datetime.now(UTC),
        )
        self.stores.state_store.save(record)

        loaded = self.stores.state_store.load("wf-state-1")
        self.assertEqual(loaded.workflow_type, "auditflow_cycle")
        self.assertEqual(loaded.state["current_state"], "mapping")

    def test_checkpoint_replay_and_outbox_round_trip(self) -> None:
        self.stores.checkpoint_store.save(
            WorkflowCheckpoint(
                workflow_run_id="wf-runtime-1",
                workflow_type="opsgraph_incident",
                checkpoint_seq=1,
                node_name="communicate",
                state_before="communicate",
                state_after="resolve",
                state_patch={"current_state": "resolve"},
                warning_codes=[],
                recorded_at=datetime.now(UTC),
            )
        )
        self.stores.replay_store.save(
            ReplayRecord(
                workflow_run_id="wf-runtime-1",
                workflow_type="opsgraph_incident",
                checkpoint_seq=1,
                bundle_id="opsgraph.comms",
                bundle_version="2026-03-16.1",
                model_profile_id="generation.grounded",
                response_schema_ref="opsgraph.comms.output.v1",
                tool_manifest_names=["incident.read_timeline"],
                input_variable_names=["incident_id", "current_fact_set_version"],
                output_summary="Generated draft.",
                recorded_at=datetime.now(UTC),
            )
        )
        self.stores.outbox_store.append(
            OutboxEvent(
                event_id="evt-sql-1",
                event_name="opsgraph.comms.ready",
                workflow_run_id="wf-runtime-1",
                workflow_type="opsgraph_incident",
                node_name="communicate",
                aggregate_type="incident",
                aggregate_id="incident-1",
                payload={"current_state": "resolve"},
                emitted_at=datetime.now(UTC),
            )
        )

        checkpoints = self.stores.checkpoint_store.list_for_run("wf-runtime-1")
        replays = self.stores.replay_store.list_for_run("wf-runtime-1")
        pending = self.stores.outbox_store.list_pending()

        self.assertEqual(checkpoints[0].state_after, "resolve")
        self.assertEqual(replays[0].bundle_id, "opsgraph.comms")
        self.assertEqual(pending[0].event.event_name, "opsgraph.comms.ready")

        self.stores.outbox_store.mark_dispatched("evt-sql-1", datetime.now(UTC))
        self.assertEqual(self.stores.outbox_store.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
