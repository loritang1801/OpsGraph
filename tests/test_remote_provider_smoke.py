from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.connectors import RemoteToolFetchResult
from opsgraph_app.remote_provider_smoke import (
    available_smoke_providers,
    run_remote_provider_smoke,
    run_remote_provider_smoke_suite,
)


class _FakeResolver:
    def __init__(
        self,
        *,
        capabilities: dict[str, list[dict[str, object]]],
        results: dict[str, RemoteToolFetchResult | None],
    ) -> None:
        self._capabilities = {key: list(value) for key, value in capabilities.items()}
        self._results = dict(results)

    def describe_capability(self, provider: str, *, local_backend_id: str, remote_backend_id: str) -> dict[str, object]:
        del local_backend_id, remote_backend_id
        queue = self._capabilities[provider]
        if len(queue) > 1:
            return dict(queue.pop(0))
        return dict(queue[0])

    def fetch_deployments(self, **kwargs):
        del kwargs
        return self._results.get("deployment_lookup")

    def fetch_services(self, **kwargs):
        del kwargs
        return self._results.get("service_registry")

    def fetch_runbooks(self, **kwargs):
        del kwargs
        return self._results.get("runbook_search")

    def fetch_change_context(self, **kwargs):
        del kwargs
        return self._results.get("change_context")

    def publish_comms(self, **kwargs):
        del kwargs
        return self._results.get("comms_publish")


class OpsGraphRemoteProviderSmokeTests(unittest.TestCase):
    def test_default_provider_list_excludes_write_provider(self) -> None:
        self.assertEqual(
            available_smoke_providers(),
            ["deployment_lookup", "service_registry", "runbook_search", "change_context"],
        )
        self.assertIn("comms_publish", available_smoke_providers(include_write=True))

    def test_run_remote_provider_smoke_skips_inactive_remote_provider(self) -> None:
        resolver = _FakeResolver(
            capabilities={
                "deployment_lookup": [
                    {
                        "requested_mode": "auto",
                        "effective_mode": "local",
                        "backend_id": "heuristic-github-adapter",
                        "fallback_reason": "OPSGRAPH_DEPLOYMENT_LOOKUP_HTTP_TEMPLATE_NOT_CONFIGURED",
                        "details": {"fallback_enabled": True},
                    }
                ]
            },
            results={},
        )

        result = run_remote_provider_smoke(
            resolver,
            "deployment_lookup",
            params={"service_id": "checkout-api", "incident_id": "incident-1", "limit": 2},
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "OPSGRAPH_DEPLOYMENT_LOOKUP_HTTP_TEMPLATE_NOT_CONFIGURED")

    def test_run_remote_provider_smoke_reports_runtime_fallback_failure(self) -> None:
        resolver = _FakeResolver(
            capabilities={
                "runbook_search": [
                    {
                        "requested_mode": "auto",
                        "effective_mode": "http",
                        "backend_id": "http-runbook-provider",
                        "fallback_reason": None,
                        "details": {},
                    },
                    {
                        "requested_mode": "auto",
                        "effective_mode": "local",
                        "backend_id": "heuristic-runbook-index",
                        "fallback_reason": "OPSGRAPH_RUNBOOK_SEARCH_REMOTE_REQUEST_FAILED",
                        "details": {"last_remote_error": "RuntimeError"},
                    },
                ]
            },
            results={"runbook_search": None},
        )

        result = run_remote_provider_smoke(
            resolver,
            "runbook_search",
            params={"service_id": "checkout-api", "runbook_query": "rollback elevated 5xx", "limit": 2},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "OPSGRAPH_RUNBOOK_SEARCH_REMOTE_REQUEST_FAILED")

    def test_run_remote_provider_smoke_validates_successful_response(self) -> None:
        resolver = _FakeResolver(
            capabilities={
                "service_registry": [
                    {
                        "requested_mode": "http",
                        "effective_mode": "http",
                        "backend_id": "http-service-registry-provider",
                        "fallback_reason": None,
                        "details": {},
                    }
                ]
            },
            results={
                "service_registry": RemoteToolFetchResult(
                    normalized_payload={
                        "services": [
                            {
                                "service_id": "checkout-api",
                                "name": "Checkout API",
                                "owner_team": "payments-sre",
                                "dependency_names": ["postgres"],
                                "runbook_refs": ["runbook-checkout-api-rollback"],
                            }
                        ]
                    },
                    source_locator="https://services.example.test/registry?service=checkout-api",
                    connection_id="service-registry-http",
                )
            },
        )

        result = run_remote_provider_smoke(
            resolver,
            "service_registry",
            params={
                "service_id": "checkout-api",
                "search_query": "checkout api",
                "limit": 5,
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["response"]["services"][0]["service_id"], "checkout-api")
        self.assertEqual(result["provenance"]["connection_id"], "service-registry-http")

    def test_smoke_suite_require_configured_fails_on_skipped_provider(self) -> None:
        resolver = _FakeResolver(
            capabilities={
                "deployment_lookup": [
                    {
                        "requested_mode": "auto",
                        "effective_mode": "local",
                        "backend_id": "heuristic-github-adapter",
                        "fallback_reason": "OPSGRAPH_DEPLOYMENT_LOOKUP_HTTP_TEMPLATE_NOT_CONFIGURED",
                        "details": {},
                    }
                ]
            },
            results={},
        )

        payload = run_remote_provider_smoke_suite(
            resolver=resolver,
            providers=["deployment_lookup"],
            require_configured=True,
        )

        self.assertEqual(payload["summary"]["skipped_count"], 1)
        self.assertEqual(payload["exit_code"], 1)
