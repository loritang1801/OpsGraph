from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "remote_provider_contracts"
SCHEMAS = ROOT / "schemas" / "remote_provider_contracts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.remote_provider_schemas import (
    build_remote_provider_schema_documents,
    build_remote_provider_schema_models,
)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class OpsGraphRemoteProviderSchemaTests(unittest.TestCase):
    def test_stored_remote_provider_schema_files_match_current_model_generation(self) -> None:
        expected_documents = build_remote_provider_schema_documents()

        for filename, expected_document in expected_documents.items():
            actual_document = _load_json(SCHEMAS / filename)
            self.assertEqual(actual_document, expected_document, filename)

    def test_canonical_remote_provider_fixtures_validate_against_schema_models(self) -> None:
        schema_models = build_remote_provider_schema_models()
        fixture_map = {
            "deployment_lookup_request.schema.json": "deployment_lookup_request.json",
            "deployment_lookup_response.schema.json": "deployment_lookup_response.json",
            "service_registry_request.schema.json": "service_registry_request.json",
            "service_registry_response.schema.json": "service_registry_response.json",
            "runbook_search_request.schema.json": "runbook_search_request.json",
            "runbook_search_response.schema.json": "runbook_search_response.json",
        }

        for schema_filename, fixture_filename in fixture_map.items():
            model = schema_models[schema_filename]
            fixture_payload = _load_json(FIXTURES / fixture_filename)
            payload = {
                field_name: fixture_payload[field_name]
                for field_name in model.model_fields
                if field_name in fixture_payload
            }
            validated = model.model_validate(payload)
            self.assertEqual(validated.model_dump(mode="json"), payload, schema_filename)


if __name__ == "__main__":
    unittest.main()
