from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.routes import map_domain_error, paginate_collection, success_envelope


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


if __name__ == "__main__":
    unittest.main()
