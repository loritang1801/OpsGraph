from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.routes import map_domain_error


class OpsGraphRouteErrorMappingTests(unittest.TestCase):
    def test_maps_incident_not_found_key_error_to_404(self) -> None:
        status_code, payload = map_domain_error(KeyError("incident-404"), path="/api/v1/opsgraph/incidents/incident-404")

        self.assertEqual(status_code, 404)
        self.assertEqual(payload["error"]["code"], "INCIDENT_NOT_FOUND")

    def test_maps_conflict_value_error_to_409(self) -> None:
        status_code, payload = map_domain_error(ValueError("APPROVAL_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/comms")

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"]["code"], "APPROVAL_REQUIRED")

    def test_maps_validation_value_error_to_422(self) -> None:
        status_code, payload = map_domain_error(ValueError("ROOT_CAUSE_FACT_REQUIRED"), path="/api/v1/opsgraph/incidents/incident-1/resolve")

        self.assertEqual(status_code, 422)
        self.assertEqual(payload["error"]["code"], "ROOT_CAUSE_FACT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
