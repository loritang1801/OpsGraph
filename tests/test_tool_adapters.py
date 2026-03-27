from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.connectors import EnvConfiguredOpsGraphRemoteToolResolver
from opsgraph_app.tool_adapters import GitHubDeploymentAdapter, RunbookSearchAdapter, ServiceRegistryAdapter


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        json_payload=None,
        text: str = "",
        url: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text
        self.url = url

    def json(self):
        if self._json_payload is None:
            raise ValueError("No JSON payload configured")
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    def __init__(self, *, response: _FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
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
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise RuntimeError("No response configured")
        return self.response


class OpsGraphToolAdapterTests(unittest.TestCase):
    def test_deployment_lookup_adapter_fetches_remote_provider_when_configured(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            response=_FakeResponse(
                status_code=200,
                json_payload={
                    "deployments": [
                        {
                            "id": "deploy-remote-1",
                            "commit_sha": "9f8e7d6c5b4a",
                            "actor": {"login": "release-bot"},
                            "deployed_at": "2026-03-27T01:02:03Z",
                        }
                    ]
                },
                url="https://deployments.example.test/services/checkout-api/deployments?incident=incident-1&limit=2",
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
                "OPSGRAPH_DEPLOYMENT_LOOKUP_URL_TEMPLATE": (
                    "https://deployments.example.test/services/{service_id}/deployments"
                    "?incident={incident_id}&limit={limit}"
                ),
                "OPSGRAPH_DEPLOYMENT_LOOKUP_AUTH_TOKEN": "deploy-token",
                "OPSGRAPH_DEPLOYMENT_LOOKUP_CONNECTION_ID": "deployment-http",
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="deployment.lookup", adapter_type="github"),
                call=SimpleNamespace(subject_type="incident", subject_id="incident-1"),
                arguments=SimpleNamespace(service_id="checkout-api", incident_id="incident-1", limit=2),
            )

        self.assertEqual(result["normalized_payload"]["deployments"][0]["deployment_id"], "deploy-remote-1")
        self.assertEqual(result["normalized_payload"]["deployments"][0]["actor"], "release-bot")
        self.assertEqual(result["provenance"]["connection_id"], "deployment-http")
        self.assertEqual(
            fake_client.calls[0]["url"],
            "https://deployments.example.test/services/checkout-api/deployments?incident=incident-1&limit=2",
        )
        self.assertEqual(fake_client.calls[0]["headers"]["Authorization"], "Bearer deploy-token")

    def test_runbook_search_adapter_falls_back_to_local_when_remote_provider_errors_in_auto_mode(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(error=RuntimeError("provider unavailable"))
        adapter = RunbookSearchAdapter(
            service.repository,
            remote_provider=EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client),
        )

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
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="runbook.search", adapter_type="vector_store"),
                call=SimpleNamespace(subject_type="incident", subject_id="incident-1"),
                arguments=SimpleNamespace(service_id="checkout-api", query="rollback 5xx errors", limit=2),
            )

        self.assertEqual(result["normalized_payload"]["runbooks"][0]["runbook_id"], "runbook-checkout-api-rollback")
        self.assertEqual(
            result["provenance"]["source_locator"],
            "opsgraph://runbooks/checkout-api?query=rollback 5xx errors",
        )
        self.assertEqual(
            fake_client.calls[0]["url"],
            "https://runbooks.example.test/search?service=checkout-api&q=rollback%205xx%20errors&limit=2",
        )

    def test_service_registry_adapter_fetches_remote_provider_when_configured(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            response=_FakeResponse(
                status_code=200,
                json_payload={
                    "services": [
                        {
                            "service_id": "checkout-api",
                            "name": "Checkout API",
                            "owner": {"team": "payments-sre"},
                            "dependencies": ["postgres", "redis"],
                            "runbooks": [{"runbook_id": "runbook-checkout-api-rollback"}],
                        }
                    ]
                },
                url="https://services.example.test/registry?service=checkout-api&query=checkout%20api&limit=5",
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
                "OPSGRAPH_SERVICE_REGISTRY_URL_TEMPLATE": (
                    "https://services.example.test/registry?service={service_id}&query={search_query}&limit={limit}"
                ),
                "OPSGRAPH_SERVICE_REGISTRY_CONNECTION_ID": "service-registry-http",
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="service_registry.lookup", adapter_type="service_registry"),
                call=SimpleNamespace(subject_type="incident", subject_id="incident-1"),
                arguments=SimpleNamespace(service_id="checkout-api", search_query="checkout api"),
            )

        self.assertEqual(result["normalized_payload"]["services"][0]["service_id"], "checkout-api")
        self.assertEqual(result["normalized_payload"]["services"][0]["owner_team"], "payments-sre")
        self.assertEqual(result["normalized_payload"]["services"][0]["runbook_refs"], ["runbook-checkout-api-rollback"])
        self.assertEqual(result["provenance"]["connection_id"], "service-registry-http")
        self.assertEqual(
            fake_client.calls[0]["url"],
            "https://services.example.test/registry?service=checkout-api&query=checkout%20api&limit=5",
        )


if __name__ == "__main__":
    unittest.main()
