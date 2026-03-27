from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_platform import (
    assert_collection_contains,
    assert_collection_size,
    assert_domain_error_mapping,
    assert_event_topic_routing,
    assert_fields,
    assert_health_response,
    assert_json_data_response,
    assert_json_error_response,
    assert_paginated_window,
    assert_resume_after_id_contract,
    assert_sse_message_contract,
    assert_success_envelope,
    assert_managed_app_service,
    bearer_auth_headers,
    header_auth_headers,
    create_managed_test_client,
    fastapi_test_client_class,
    get_current_user_via_bearer,
    login_via_session_route,
    refresh_session_access_token,
    revoke_current_session,
    request_with_header_auth,
    request_with_header_auth_and_assert_json_error,
    request_with_header_auth_and_get_data,
    request_with_bearer_and_assert_json_error,
    request_with_bearer_and_get_data,
    request_with_bearer,
    request_and_get_data,
    request_and_assert_json_error,
)


class TestSupportTests(unittest.TestCase):
    def test_fastapi_test_client_class_returns_none_when_fastapi_is_missing(self) -> None:
        with patch.dict(sys.modules, {"fastapi": None, "fastapi.testclient": None}):
            self.assertIsNone(fastapi_test_client_class())

    def test_create_managed_test_client_registers_client_close_cleanup(self) -> None:
        closed: list[str] = []

        class _FakeClient:
            def __init__(self, app) -> None:
                self.app = app

            def close(self) -> None:
                closed.append("closed")

        case = unittest.TestCase()
        with patch("agent_platform.test_support.fastapi_test_client_class", return_value=_FakeClient):
            client = create_managed_test_client(case, app=SimpleNamespace())

        self.assertIsInstance(client, _FakeClient)
        case.doCleanups()
        self.assertEqual(closed, ["closed"])

    def test_assert_managed_app_service_registers_service_cleanup(self) -> None:
        closed: list[str] = []

        class _ClosableService:
            def close(self) -> None:
                closed.append("closed")

        case = unittest.TestCase()
        app = SimpleNamespace(state=SimpleNamespace(service_ref=_ClosableService()))

        service = assert_managed_app_service(case, app, state_attr="service_ref")

        self.assertIs(app.state.service_ref, service)
        case.doCleanups()
        self.assertEqual(closed, ["closed"])

    def test_assert_json_error_response_checks_status_and_code(self) -> None:
        response = SimpleNamespace(
            status_code=403,
            json=lambda: {"error": {"code": "AUTH_FORBIDDEN"}},
        )

        payload = assert_json_error_response(
            self,
            response,
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

        self.assertEqual(payload["error"]["code"], "AUTH_FORBIDDEN")

    def test_assert_domain_error_mapping_checks_status_and_code(self) -> None:
        payload = assert_domain_error_mapping(
            self,
            (
                404,
                {"error": {"code": "INCIDENT_NOT_FOUND"}},
            ),
            status_code=404,
            error_code="INCIDENT_NOT_FOUND",
        )

        self.assertEqual(payload["error"]["code"], "INCIDENT_NOT_FOUND")

    def test_assert_json_data_response_returns_data_payload(self) -> None:
        response = SimpleNamespace(
            status_code=201,
            json=lambda: {"data": {"id": "item-1"}},
        )

        data = assert_json_data_response(self, response, status_code=201)

        self.assertEqual(data["id"], "item-1")

    def test_assert_collection_size_supports_exact_and_minimum_checks(self) -> None:
        actual_size = assert_collection_size(self, [{"id": "one"}], size=1, min_size=1)

        self.assertEqual(actual_size, 1)

    def test_assert_collection_contains_matches_nested_dict_fields(self) -> None:
        item = assert_collection_contains(
            self,
            [
                {"user": {"email": "viewer@example.com"}, "role": "viewer"},
                {"user": {"email": "admin@example.com"}, "role": "org_admin"},
            ],
            expected_fields={"user.email": "admin@example.com", "role": "org_admin"},
        )

        self.assertEqual(item["role"], "org_admin")

    def test_assert_collection_contains_matches_object_attributes(self) -> None:
        item = assert_collection_contains(
            self,
            [
                SimpleNamespace(
                    workflow_run_id="wf-1",
                    tool=SimpleNamespace(name="mapping.read_candidates"),
                )
            ],
            expected_fields={"workflow_run_id": "wf-1", "tool.name": "mapping.read_candidates"},
        )

        self.assertEqual(item.workflow_run_id, "wf-1")

    def test_assert_fields_matches_nested_dict_and_list_paths(self) -> None:
        payload = assert_fields(
            self,
            {
                "active_organization": {"slug": "acme"},
                "items": [{"evidence_chunk_id": "chunk-1"}],
            },
            expected_fields={
                "active_organization.slug": "acme",
                "items.0.evidence_chunk_id": "chunk-1",
            },
        )

        self.assertEqual(payload["active_organization"]["slug"], "acme")

    def test_assert_fields_matches_object_attributes(self) -> None:
        payload = assert_fields(
            self,
            SimpleNamespace(model_provider=SimpleNamespace(effective_mode="local")),
            expected_fields={"model_provider.effective_mode": "local"},
        )

        self.assertEqual(payload.model_provider.effective_mode, "local")

    def test_assert_success_envelope_matches_data_and_meta_fields(self) -> None:
        payload = assert_success_envelope(
            self,
            {
                "data": {"package_id": "pkg-1"},
                "meta": {"request_id": "req-1", "workflow_run_id": "wf-1", "has_more": False},
            },
            data_expected_fields={"package_id": "pkg-1"},
            meta_expected_fields={
                "request_id": "req-1",
                "workflow_run_id": "wf-1",
                "has_more": False,
            },
        )

        self.assertEqual(payload["data"]["package_id"], "pkg-1")

    def test_assert_success_envelope_reads_response_json(self) -> None:
        response = SimpleNamespace(
            json=lambda: {
                "data": {"items": [{"id": "incident-1"}]},
                "meta": {"request_id": "req-2", "next_cursor": "cursor-1", "has_more": True},
            }
        )

        payload = assert_success_envelope(
            self,
            response,
            data_expected_fields={"items.0.id": "incident-1"},
            meta_expected_fields={"request_id": "req-2", "next_cursor": "cursor-1", "has_more": True},
        )

        self.assertEqual(payload["meta"]["next_cursor"], "cursor-1")

    def test_assert_sse_message_contract_checks_message_lines(self) -> None:
        message = assert_sse_message_contract(
            self,
            "id: evt-1\nevent: demo.ready\ndata: {\"workspace_id\": \"ws-1\"}\n\n",
            event_id="evt-1",
            event_name="demo.ready",
            expected_substrings=['"workspace_id": "ws-1"'],
            resolved_topic="demo",
            expected_topic="demo",
        )

        self.assertIn("event: demo.ready", message)

    def test_assert_paginated_window_checks_items_and_cursor_presence(self) -> None:
        next_cursor = assert_paginated_window(
            self,
            ([1, 2], "cursor-1", True),
            expected_items=[1, 2],
            has_more=True,
            next_cursor_present=True,
        )

        self.assertEqual(next_cursor, "cursor-1")

    def test_assert_paginated_window_checks_absent_cursor(self) -> None:
        next_cursor = assert_paginated_window(
            self,
            ([3], None, False),
            expected_items=[3],
            has_more=False,
            next_cursor_present=False,
        )

        self.assertIsNone(next_cursor)

    def test_assert_event_topic_routing_checks_topics_and_match_results(self) -> None:
        topics = assert_event_topic_routing(
            self,
            {"subject_id": "incident-1"},
            ["opsgraph.workspace.ops-ws-1", "opsgraph.incident.incident-1"],
            expected_topics=["opsgraph.workspace.ops-ws-1", "opsgraph.incident.incident-1"],
            matcher=lambda context, topic: context["subject_id"] in topic,
            matching_topic="opsgraph.incident.incident-1",
            rejected_topic="workflow",
        )

        self.assertEqual(len(topics), 2)

    def test_assert_resume_after_id_contract_checks_missing_and_existing_ids(self) -> None:
        pending = [SimpleNamespace(event=SimpleNamespace(event_id="evt-1"))]

        assert_resume_after_id_contract(
            self,
            lambda items, value: next(
                (entry.event.event_id for entry in items if entry.event.event_id == value),
                None,
            ),
            pending,
            missing_id="evt-missing",
            existing_id="evt-1",
        )

    def test_request_and_assert_json_error_wraps_client_request(self) -> None:
        captured: dict[str, object] = {}

        class _Client:
            def request(self, method, path, **kwargs):
                captured["method"] = method
                captured["path"] = path
                captured["kwargs"] = kwargs
                return SimpleNamespace(
                    status_code=401,
                    json=lambda: {"error": {"code": "AUTH_REQUIRED"}},
                )

        response = request_and_assert_json_error(
            self,
            _Client(),
            "GET",
            "/demo",
            status_code=401,
            error_code="AUTH_REQUIRED",
            headers={"X-Organization-Id": "org-1"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/demo")

    def test_request_and_get_data_wraps_client_request(self) -> None:
        captured: dict[str, object] = {}

        class _Client:
            def request(self, method, path, **kwargs):
                captured["method"] = method
                captured["path"] = path
                captured["kwargs"] = kwargs
                return SimpleNamespace(
                    status_code=201,
                    json=lambda: {"data": {"id": "item-1"}},
                )

        response, data = request_and_get_data(
            self,
            _Client(),
            "POST",
            "/demo",
            status_code=201,
            headers={"X-Request-Id": "req-1"},
            json={"name": "demo"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(data["id"], "item-1")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/demo")

    def test_assert_health_response_checks_ok_payload(self) -> None:
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"data": {"status": "ok", "product": "demo"}},
        )

        payload = assert_health_response(self, response, product="demo")

        self.assertEqual(payload["data"]["product"], "demo")

    def test_login_via_session_route_returns_access_token(self) -> None:
        class _Client:
            def post(self, path, json):
                self.path = path
                self.json_payload = json
                return SimpleNamespace(
                    status_code=200,
                    cookies={"refresh_token": "refresh-1"},
                    json=lambda: {"data": {"access_token": "access-1"}},
                )

        client = _Client()

        response, access_token = login_via_session_route(
            self,
            client,
            email="viewer@example.com",
            password="secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(access_token, "access-1")
        self.assertEqual(client.path, "/api/v1/auth/session")

    def test_refresh_session_access_token_returns_new_token(self) -> None:
        class _Client:
            def post(self, path):
                self.path = path
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"data": {"access_token": "access-2"}},
                )

        client = _Client()

        response, access_token = refresh_session_access_token(self, client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(access_token, "access-2")
        self.assertEqual(client.path, "/api/v1/auth/session/refresh")

    def test_revoke_current_session_uses_bearer_token(self) -> None:
        class _Client:
            def request(self, method, path, headers):
                self.method = method
                self.path = path
                self.headers = headers
                return SimpleNamespace(status_code=204)

        client = _Client()

        response = revoke_current_session(self, client, access_token="access-3")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.method, "DELETE")
        self.assertEqual(client.path, "/api/v1/auth/session/current")
        self.assertEqual(client.headers["Authorization"], "Bearer access-3")

    def test_bearer_auth_headers_merges_extra_headers(self) -> None:
        headers = bearer_auth_headers("access-4", headers={"Idempotency-Key": "req-1"})

        self.assertEqual(headers["Authorization"], "Bearer access-4")
        self.assertEqual(headers["Idempotency-Key"], "req-1")

    def test_header_auth_headers_adds_shared_header_contract(self) -> None:
        headers = header_auth_headers(
            role="operator",
            organization_id="org-9",
            user_id="user-8",
            access_token="access-4",
            headers={"Idempotency-Key": "req-2"},
        )

        self.assertEqual(headers["Authorization"], "Bearer access-4")
        self.assertEqual(headers["X-Organization-Id"], "org-9")
        self.assertEqual(headers["X-User-Id"], "user-8")
        self.assertEqual(headers["X-User-Role"], "operator")
        self.assertEqual(headers["Idempotency-Key"], "req-2")

    def test_request_with_bearer_builds_authorization_header(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(status_code=200)

        client = _Client()

        response = request_with_bearer(
            client,
            "PATCH",
            "/demo",
            access_token="access-5",
            headers={"Idempotency-Key": "req-2"},
            json={"status": "ok"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.method, "PATCH")
        self.assertEqual(client.path, "/demo")
        self.assertEqual(client.headers["Authorization"], "Bearer access-5")
        self.assertEqual(client.headers["Idempotency-Key"], "req-2")
        self.assertEqual(client.kwargs["json"]["status"], "ok")

    def test_request_with_header_auth_builds_shared_auth_headers(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(status_code=200)

        client = _Client()

        response = request_with_header_auth(
            client,
            "GET",
            "/demo",
            role="reviewer",
            headers={"Idempotency-Key": "req-3"},
            params={"cursor": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.headers["Authorization"], "Bearer test-token")
        self.assertEqual(client.headers["X-Organization-Id"], "org-1")
        self.assertEqual(client.headers["X-User-Id"], "user-1")
        self.assertEqual(client.headers["X-User-Role"], "reviewer")
        self.assertEqual(client.headers["Idempotency-Key"], "req-3")
        self.assertEqual(client.kwargs["params"]["cursor"], "1")

    def test_request_with_bearer_and_assert_json_error_checks_error_payload(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(
                    status_code=403,
                    json=lambda: {"error": {"code": "AUTH_FORBIDDEN"}},
                )

        client = _Client()

        response = request_with_bearer_and_assert_json_error(
            self,
            client,
            "GET",
            "/protected",
            access_token="access-6",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.headers["Authorization"], "Bearer access-6")

    def test_request_with_header_auth_and_assert_json_error_checks_error_payload(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(
                    status_code=403,
                    json=lambda: {"error": {"code": "AUTH_FORBIDDEN"}},
                )

        client = _Client()

        response = request_with_header_auth_and_assert_json_error(
            self,
            client,
            "POST",
            "/protected",
            role="viewer",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            headers={"Idempotency-Key": "req-4"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.headers["X-User-Role"], "viewer")
        self.assertEqual(client.headers["Idempotency-Key"], "req-4")

    def test_request_with_bearer_and_get_data_returns_data_payload(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(
                    status_code=202,
                    json=lambda: {"data": {"workflow_run_id": "wf-1"}},
                )

        client = _Client()

        response, data = request_with_bearer_and_get_data(
            self,
            client,
            "POST",
            "/workflows",
            access_token="access-7",
            status_code=202,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(data["workflow_run_id"], "wf-1")

    def test_request_with_header_auth_and_get_data_returns_data_payload(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(
                    status_code=201,
                    json=lambda: {"data": {"id": "item-2"}},
                )

        client = _Client()

        response, data = request_with_header_auth_and_get_data(
            self,
            client,
            "POST",
            "/records",
            role="org_admin",
            status_code=201,
            headers={"Idempotency-Key": "req-5"},
            json={"name": "demo"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(data["id"], "item-2")
        self.assertEqual(client.headers["X-User-Role"], "org_admin")
        self.assertEqual(client.kwargs["json"]["name"], "demo")

    def test_get_current_user_via_bearer_returns_data_payload(self) -> None:
        class _Client:
            def request(self, method, path, headers, **kwargs):
                self.method = method
                self.path = path
                self.headers = headers
                self.kwargs = kwargs
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"data": {"active_organization": {"slug": "acme"}}},
                )

        client = _Client()

        response, data = get_current_user_via_bearer(self, client, access_token="access-6")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.method, "GET")
        self.assertEqual(client.path, "/api/v1/me")
        self.assertEqual(data["active_organization"]["slug"], "acme")


if __name__ == "__main__":
    unittest.main()
