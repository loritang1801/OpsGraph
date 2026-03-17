from __future__ import annotations

import unittest
from datetime import UTC, datetime

from agent_platform import StaticToolAdapter, ToolExecutor, build_default_runtime_catalog
from agent_platform.errors import ToolAdapterNotRegisteredError, ToolExecutionError
from agent_platform.shared import AuthorizationContext, ToolCallEnvelope


class ToolExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_default_runtime_catalog()
        self.executor = ToolExecutor(self.catalog)

    def test_executes_registered_adapter_and_validates_output(self) -> None:
        self.executor.register_adapter(
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
                        "fetched_at": datetime.now(UTC),
                        "source_locator": "artifact-1",
                    },
                    "raw_ref": {"artifact_id": "artifact-1", "kind": "external_payload"},
                    "warnings": [],
                }
            ),
        )

        outcome = self.executor.execute(
            ToolCallEnvelope(
                tool_call_id="call-1",
                tool_name="artifact.read",
                tool_version="2026-03-16.1",
                workflow_run_id="wf-1",
                subject_type="audit_cycle",
                subject_id="cycle-1",
                arguments={"artifact_id": "artifact-1"},
                idempotency_key="wf-1:artifact.read:1",
                authorization_context=AuthorizationContext(
                    organization_id="org-1",
                    workspace_id="ws-1",
                    connection_id="conn-1",
                ),
            )
        )

        self.assertEqual(outcome.envelope.normalized_payload["artifact_id"], "artifact-1")
        self.assertEqual(outcome.trace.adapter_type, "artifact_store")

    def test_raises_for_missing_adapter(self) -> None:
        with self.assertRaises(ToolAdapterNotRegisteredError):
            self.executor.execute(
                ToolCallEnvelope(
                    tool_call_id="call-2",
                    tool_name="artifact.read",
                    tool_version="2026-03-16.1",
                    workflow_run_id="wf-2",
                    subject_type="audit_cycle",
                    subject_id="cycle-1",
                    arguments={"artifact_id": "artifact-1"},
                    idempotency_key="wf-2:artifact.read:1",
                    authorization_context=AuthorizationContext(
                        organization_id="org-1",
                        workspace_id="ws-1",
                    ),
                )
            )

    def test_raises_for_invalid_normalized_payload(self) -> None:
        self.executor.register_adapter(
            "artifact_store",
            StaticToolAdapter(
                {
                    "status": "success",
                    "normalized_payload": {
                        "artifact_type": "upload",
                        "parser_status": "completed",
                    },
                    "provenance": {
                        "adapter_type": "artifact_store",
                        "connection_id": "conn-1",
                        "fetched_at": datetime.now(UTC),
                        "source_locator": "artifact-1",
                    },
                    "warnings": [],
                }
            ),
        )

        with self.assertRaises(ToolExecutionError):
            self.executor.execute(
                ToolCallEnvelope(
                    tool_call_id="call-3",
                    tool_name="artifact.read",
                    tool_version="2026-03-16.1",
                    workflow_run_id="wf-3",
                    subject_type="audit_cycle",
                    subject_id="cycle-1",
                    arguments={"artifact_id": "artifact-1"},
                    idempotency_key="wf-3:artifact.read:1",
                    authorization_context=AuthorizationContext(
                        organization_id="org-1",
                        workspace_id="ws-1",
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()
