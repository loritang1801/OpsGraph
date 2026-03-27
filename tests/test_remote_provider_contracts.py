from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "remote_provider_contracts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.connectors import EnvConfiguredOpsGraphRemoteToolResolver
from opsgraph_app.tool_adapters import GitHubDeploymentAdapter, RunbookSearchAdapter, ServiceRegistryAdapter


class _FakeResponse:
    def __init__(self, *, status_code: int, json_payload: dict[str, object], url: str) -> None:
        self.status_code = status_code
        self._json_payload = json_payload
        self.url = url
        self.text = json.dumps(json_payload)

    def json(self):
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "follow_redirects": follow_redirects,
                "timeout": timeout,
            }
        )
        return self.response


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OpsGraphRemoteProviderContractTests(unittest.TestCase):
    def test_deployment_lookup_contract_fixture_round_trip(self) -> None:
        request_fixture = _load_fixture("deployment_lookup_request.json")
        response_fixture = _load_fixture("deployment_lookup_response.json")
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload=response_fixture,
                url=str(request_fixture["expected_url"]),
            )
        )
        adapter = GitHubDeploymentAdapter(
            service.repository,
            remote_provider=EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client),
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_DEPLOYMENT_LOOKUP_PROVIDER": "http",
                "OPSGRAPH_DEPLOYMENT_LOOKUP_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="deployment.lookup", adapter_type="github"),
                call=SimpleNamespace(subject_type="incident", subject_id=str(request_fixture["incident_id"])),
                arguments=SimpleNamespace(
                    service_id=str(request_fixture["service_id"]),
                    incident_id=str(request_fixture["incident_id"]),
                    limit=int(request_fixture["limit"]),
                ),
            )

        self.assertEqual(fake_client.calls[0]["url"], request_fixture["expected_url"])
        self.assertEqual(result["normalized_payload"], response_fixture)

    def test_service_registry_contract_fixture_round_trip(self) -> None:
        request_fixture = _load_fixture("service_registry_request.json")
        response_fixture = _load_fixture("service_registry_response.json")
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload=response_fixture,
                url=str(request_fixture["expected_url"]),
            )
        )
        adapter = ServiceRegistryAdapter(
            service.repository,
            remote_provider=EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client),
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_SERVICE_REGISTRY_PROVIDER": "http",
                "OPSGRAPH_SERVICE_REGISTRY_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="service_registry.lookup", adapter_type="service_registry"),
                call=SimpleNamespace(subject_type="incident", subject_id="incident-1"),
                arguments=SimpleNamespace(
                    service_id=str(request_fixture["service_id"]),
                    search_query=str(request_fixture["search_query"]),
                ),
            )

        self.assertEqual(fake_client.calls[0]["url"], request_fixture["expected_url"])
        self.assertEqual(result["normalized_payload"], response_fixture)

    def test_runbook_search_contract_fixture_round_trip(self) -> None:
        request_fixture = _load_fixture("runbook_search_request.json")
        response_fixture = _load_fixture("runbook_search_response.json")
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload=response_fixture,
                url=str(request_fixture["expected_url"]),
            )
        )
        adapter = RunbookSearchAdapter(
            service.repository,
            remote_provider=EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client),
        )

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_RUNBOOK_SEARCH_PROVIDER": "http",
                "OPSGRAPH_RUNBOOK_SEARCH_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="runbook.search", adapter_type="vector_store"),
                call=SimpleNamespace(subject_type="incident", subject_id="incident-1"),
                arguments=SimpleNamespace(
                    service_id=str(request_fixture["service_id"]),
                    query=str(request_fixture["query"]),
                    limit=int(request_fixture["limit"]),
                ),
            )

        self.assertEqual(fake_client.calls[0]["url"], request_fixture["expected_url"])
        self.assertEqual(result["normalized_payload"], response_fixture)


if __name__ == "__main__":
    unittest.main()
