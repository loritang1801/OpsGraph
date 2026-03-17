from __future__ import annotations

import tempfile
import unittest

from agent_platform import FileReplayFixtureStore, ReplayFixture
from agent_platform.shared import SharedAgentOutputEnvelope


class FileReplayStoreTests(unittest.TestCase):
    def test_save_load_and_list_fixture_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileReplayFixtureStore(temp_dir)
            fixture = ReplayFixture(
                fixture_key="wf-1:1:normalization",
                workflow_type="auditflow_cycle",
                node_name="normalization",
                bundle_id="auditflow.collector",
                bundle_version="2026-03-16.1",
                expected_output=SharedAgentOutputEnvelope(
                    status="success",
                    summary="Collected evidence.",
                    structured_output={
                        "normalized_title": "Quarterly Access Review",
                        "evidence_type": "ticket",
                        "summary": "Quarterly user access review completed for production systems.",
                        "captured_at": None,
                        "fresh_until": None,
                        "citation_refs": [{"kind": "artifact", "id": "artifact-1"}],
                    },
                    citations=[{"kind": "artifact", "id": "artifact-1"}],
                ),
            )
            store.save(fixture)

            loaded = store.get("wf-1:1:normalization")
            self.assertEqual(loaded.bundle_id, "auditflow.collector")
            self.assertEqual(store.list_keys(), ["wf-1:1:normalization"])


if __name__ == "__main__":
    unittest.main()
