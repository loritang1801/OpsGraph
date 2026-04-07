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
from opsgraph_app.tool_adapters import (
    ContextBundleReaderAdapter,
    GitHubDeploymentAdapter,
    RunbookSearchAdapter,
    ServiceRegistryAdapter,
)


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
    def __init__(self, response: _FakeResponse, *, post_response: _FakeResponse | None = None) -> None:
        self.response = response
        self.post_response = post_response or response
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "headers": dict(headers),
                "follow_redirects": follow_redirects,
                "timeout": timeout,
            }
        )
        return self.response

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        follow_redirects: bool,
        timeout: float,
    ):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": dict(headers),
                "json": dict(json),
                "follow_redirects": follow_redirects,
                "timeout": timeout,
            }
        )
        return self.post_response


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

    def test_change_context_contract_fixture_round_trip(self) -> None:
        request_fixture = _load_fixture("change_context_request.json")
        response_fixture = _load_fixture("change_context_response.json")
        service = build_app_service()
        self.addCleanup(service.close)
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload=response_fixture,
                url=str(request_fixture["expected_url"]),
            )
        )
        adapter = ContextBundleReaderAdapter(
            service.repository,
            remote_provider=EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client),
        )
        service.repository.get_incident_execution_seed("incident-1")

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_CHANGE_CONTEXT_PROVIDER": "http",
                "OPSGRAPH_CHANGE_CONTEXT_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = adapter.execute(
                tool=SimpleNamespace(tool_name="context_bundle.read", adapter_type="context_bundle_reader"),
                call=SimpleNamespace(subject_type="incident", subject_id=str(request_fixture["incident_id"])),
                arguments=SimpleNamespace(
                    incident_id=str(request_fixture["incident_id"]),
                    context_bundle_id="context-incident-1-v1",
                ),
            )

        self.assertEqual(fake_client.calls[0]["url"], request_fixture["expected_url"])
        self.assertTrue(
            any(
                item["kind"] == "change_ticket" and item["id"] == response_fixture["changes"][0]["ticket_ref"]
                for item in result["normalized_payload"]["refs"]
            )
        )

    def test_comms_publish_contract_fixture_round_trip(self) -> None:
        request_fixture = _load_fixture("comms_publish_request.json")
        response_fixture = _load_fixture("comms_publish_response.json")
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload=response_fixture,
                url=str(request_fixture["expected_url"]),
            )
        )
        resolver = EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client)

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = resolver.publish_comms(
                incident_id=str(request_fixture["incident_id"]),
                draft_id=str(request_fixture["draft_id"]),
                channel_type=str(request_fixture["channel_type"]),
                title=str(request_fixture["title"]),
                body_markdown=str(request_fixture["body_markdown"]),
                fact_set_version=int(request_fixture["fact_set_version"]),
            )

        assert result is not None
        self.assertEqual(fake_client.calls[0]["method"], "POST")
        self.assertEqual(fake_client.calls[0]["url"], request_fixture["expected_url"])
        self.assertEqual(
            fake_client.calls[0]["json"],
            {
                "incident_id": request_fixture["incident_id"],
                "draft_id": request_fixture["draft_id"],
                "channel_type": request_fixture["channel_type"],
                "title": request_fixture["title"],
                "body_markdown": request_fixture["body_markdown"],
                "fact_set_version": request_fixture["fact_set_version"],
            },
        )
        self.assertEqual(result.normalized_payload, response_fixture)

    def test_comms_publish_normalizes_delivery_state_aliases(self) -> None:
        request_fixture = _load_fixture("comms_publish_request.json")
        fake_client = _FakeHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload={
                    "message_id": "slack-msg-ops-queued",
                    "status": "queued",
                },
                url=str(request_fixture["expected_url"]),
            )
        )
        resolver = EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client)

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": str(request_fixture["url_template"]),
            },
            clear=False,
        ):
            result = resolver.publish_comms(
                incident_id=str(request_fixture["incident_id"]),
                draft_id=str(request_fixture["draft_id"]),
                channel_type=str(request_fixture["channel_type"]),
                title=str(request_fixture["title"]),
                body_markdown=str(request_fixture["body_markdown"]),
                fact_set_version=int(request_fixture["fact_set_version"]),
            )

        assert result is not None
        self.assertEqual(
            result.normalized_payload,
            {
                "published_message_ref": "slack-msg-ops-queued",
                "delivery_state": "accepted",
                "delivery_confirmed": False,
                "provider_delivery_status": "queued",
            },
        )
        self.assertTrue(any("normalized delivery_state 'queued'" in warning for warning in result.warnings))

    def test_comms_publish_can_confirm_delivery_via_status_lookup(self) -> None:
        request_fixture = _load_fixture("comms_publish_request.json")

        class _LookupHttpClient(_FakeHttpClient):
            def get(self, url: str, *, headers: dict[str, str], follow_redirects: bool, timeout: float):
                self.calls.append(
                    {
                        "method": "GET",
                        "url": url,
                        "headers": dict(headers),
                        "follow_redirects": follow_redirects,
                        "timeout": timeout,
                    }
                )
                return _FakeResponse(
                    status_code=200,
                    json_payload={
                        "message_id": "slack-msg-confirmed-1",
                        "status": "published",
                    },
                    url=url,
                )

        fake_client = _LookupHttpClient(
            _FakeResponse(
                status_code=200,
                json_payload={
                    "message_id": "slack-msg-confirmed-1",
                    "status": "queued",
                },
                url=str(request_fixture["expected_url"]),
            )
        )
        resolver = EnvConfiguredOpsGraphRemoteToolResolver(http_client=fake_client)

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": str(request_fixture["url_template"]),
                "OPSGRAPH_COMMS_PUBLISH_STATUS_URL_TEMPLATE": (
                    "https://comms.example.test/incidents/{incident_id}/drafts/{draft_id}"
                    "/delivery-status?message_ref={published_message_ref}"
                ),
            },
            clear=False,
        ):
            result = resolver.publish_comms(
                incident_id=str(request_fixture["incident_id"]),
                draft_id=str(request_fixture["draft_id"]),
                channel_type=str(request_fixture["channel_type"]),
                title=str(request_fixture["title"]),
                body_markdown=str(request_fixture["body_markdown"]),
                fact_set_version=int(request_fixture["fact_set_version"]),
            )

        assert result is not None
        self.assertEqual(result.normalized_payload["delivery_state"], "published")
        self.assertEqual(result.normalized_payload["delivery_confirmed"], True)
        self.assertEqual(result.normalized_payload["provider_delivery_status"], "published")
        self.assertTrue(
            any(
                call["method"] == "GET"
                and "delivery-status?message_ref=slack-msg-confirmed-1" in str(call["url"])
                for call in fake_client.calls
            )
        )

    def test_comms_publish_capability_reports_delivery_confirmation_support(self) -> None:
        resolver = EnvConfiguredOpsGraphRemoteToolResolver()

        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_COMMS_PUBLISH_PROVIDER": "http",
                "OPSGRAPH_COMMS_PUBLISH_URL_TEMPLATE": "https://comms.example.test/publish/{draft_id}",
                "OPSGRAPH_COMMS_PUBLISH_STATUS_URL_TEMPLATE": (
                    "https://comms.example.test/publish/{draft_id}/status/{published_message_ref}"
                ),
            },
            clear=False,
        ):
            capability = resolver.describe_capability(
                "comms_publish",
                local_backend_id="local-publish-fallback",
                remote_backend_id="http-comms-publish-provider",
            )

        self.assertEqual(capability["effective_mode"], "http")
        self.assertEqual(capability["details"]["configured"], True)
        self.assertEqual(capability["details"]["write_enabled"], True)
        self.assertEqual(capability["details"]["delivery_confirmable"], True)


if __name__ == "__main__":
    unittest.main()
