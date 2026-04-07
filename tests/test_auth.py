from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.auth import HeaderOpsGraphAuthorizer, OpsGraphAuthorizationError
from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.routes import create_fastapi_app
from opsgraph_app.shared_runtime import load_shared_agent_platform
from opsgraph_app.worker import OpsGraphReplayWorker

_AP = load_shared_agent_platform()
TestClient = _AP.fastapi_test_client_class()

_AUTH_ENV_KEYS = (
    "OPSGRAPH_ALLOW_HEADER_AUTH_FALLBACK",
    "OPSGRAPH_SEED_DEMO_AUTH",
    "OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL",
    "OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD",
    "OPSGRAPH_BOOTSTRAP_ADMIN_DISPLAY_NAME",
    "OPSGRAPH_BOOTSTRAP_ORG_SLUG",
    "OPSGRAPH_BOOTSTRAP_ORG_NAME",
)


def _create_auth_database_url() -> tuple[tempfile.TemporaryDirectory, str]:
    temp_dir = tempfile.TemporaryDirectory(prefix="opsgraph-auth-")
    database_url = f"sqlite+pysqlite:///{Path(temp_dir.name, 'opsgraph.db').resolve().as_posix()}"
    return temp_dir, database_url


def _patched_auth_env(**overrides: str) -> patch:
    values = {key: "" for key in _AUTH_ENV_KEYS}
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


class OpsGraphAuthorizationTests(unittest.TestCase):
    def test_authorizer_defaults_to_viewer_role(self) -> None:
        context = HeaderOpsGraphAuthorizer().authorize(
            required_role="viewer",
            authorization="Bearer test-token",
            organization_id="org-1",
        )

        self.assertEqual(context.organization_id, "org-1")
        self.assertEqual(context.role, "viewer")
        self.assertEqual(context.user_id, "demo-user")

    def test_authorizer_rejects_missing_authorization(self) -> None:
        with self.assertRaises(OpsGraphAuthorizationError) as context:
            HeaderOpsGraphAuthorizer().authorize(
                required_role="viewer",
                authorization=None,
                organization_id="org-1",
            )

        self.assertEqual(context.exception.code, "AUTH_REQUIRED")
        self.assertEqual(context.exception.status_code, 401)

    def test_authorizer_rejects_missing_tenant_header(self) -> None:
        with self.assertRaises(OpsGraphAuthorizationError) as context:
            HeaderOpsGraphAuthorizer().authorize(
                required_role="viewer",
                authorization="Bearer test-token",
                organization_id=None,
            )

        self.assertEqual(context.exception.code, "TENANT_CONTEXT_REQUIRED")
        self.assertEqual(context.exception.status_code, 400)

    def test_authorizer_rejects_insufficient_role(self) -> None:
        with self.assertRaises(OpsGraphAuthorizationError) as context:
            HeaderOpsGraphAuthorizer().authorize(
                required_role="operator",
                authorization="Bearer test-token",
                organization_id="org-1",
                user_role="viewer",
            )

        self.assertEqual(context.exception.code, "AUTH_FORBIDDEN")
        self.assertEqual(context.exception.status_code, 403)

    def test_authorizer_accepts_org_admin_alias_for_stronger_routes(self) -> None:
        context = HeaderOpsGraphAuthorizer().authorize(
            required_role="product_admin",
            authorization="Bearer test-token",
            organization_id="org-1",
            user_role="org_admin",
            user_id="admin-1",
        )

        self.assertEqual(context.role, "product_admin")
        self.assertEqual(context.user_id, "admin-1")


class OpsGraphAuthServiceTests(unittest.TestCase):
    def test_hybrid_authorizer_falls_back_to_header_contract(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        context = service.auth_service.build_authorizer().authorize(
            required_role="operator",
            authorization="Bearer test-token",
            organization_id="org-1",
            user_id="user-1",
            user_role="operator",
        )

        self.assertEqual(context.organization_id, "org-1")
        self.assertEqual(context.user_id, "user-1")
        self.assertEqual(context.role, "operator")
        self.assertIsNone(context.session_id)

    def test_persistent_auth_defaults_reject_header_only_fallback(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env():
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)

        with self.assertRaises(OpsGraphAuthorizationError) as auth_error:
            service.auth_service.build_authorizer().authorize(
                required_role="operator",
                authorization="Bearer test-token",
                organization_id="org-1",
                user_id="user-1",
                user_role="operator",
            )

        self.assertEqual(auth_error.exception.code, "AUTH_SESSION_REQUIRED")
        self.assertEqual(auth_error.exception.status_code, 401)

    def test_persistent_auth_defaults_do_not_seed_demo_users(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env():
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)

        with self.assertRaises(OpsGraphAuthorizationError) as auth_error:
            service.auth_service.create_session(
                {
                    "email": "admin@example.com",
                    "password": "opsgraph-demo",
                    "organization_slug": "acme",
                }
            )

        self.assertEqual(auth_error.exception.code, "AUTH_INVALID_CREDENTIALS")

    def test_persistent_auth_can_seed_bootstrap_admin(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env(
            OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL="bootstrap-admin@example.com",
            OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD="bootstrap-secret",
            OPSGRAPH_BOOTSTRAP_ADMIN_DISPLAY_NAME="Bootstrap Admin",
            OPSGRAPH_BOOTSTRAP_ORG_SLUG="bootstrap-org",
            OPSGRAPH_BOOTSTRAP_ORG_NAME="Bootstrap Org",
        ):
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "bootstrap-admin@example.com",
                "password": "bootstrap-secret",
                "organization_slug": "bootstrap-org",
            }
        )
        context = service.auth_service.build_authorizer().authorize(
            required_role="product_admin",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id=None,
        )

        self.assertEqual(issue.response.user.display_name, "Bootstrap Admin")
        self.assertEqual(issue.response.active_organization.slug, "bootstrap-org")
        self.assertEqual(context.organization_id, "org-bootstrap-1")
        self.assertEqual(context.user_id, "user-bootstrap-admin-1")
        self.assertEqual(context.role, "product_admin")
        self.assertIsNotNone(context.session_id)

    def test_persistent_auth_can_reenable_header_fallback_via_env(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env(OPSGRAPH_ALLOW_HEADER_AUTH_FALLBACK="true"):
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)

        context = service.auth_service.build_authorizer().authorize(
            required_role="operator",
            authorization="Bearer test-token",
            organization_id="org-1",
            user_id="user-1",
            user_role="operator",
        )

        self.assertEqual(context.organization_id, "org-1")
        self.assertEqual(context.user_id, "user-1")
        self.assertEqual(context.role, "operator")
        self.assertIsNone(context.session_id)

    def test_persistent_auth_can_reenable_demo_seed_via_env(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env(OPSGRAPH_SEED_DEMO_AUTH="true"):
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )

        self.assertEqual(issue.response.user.email, "admin@example.com")
        self.assertEqual(issue.response.active_organization.slug, "acme")

    def test_create_session_issue_access_token_and_authorize(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "operator@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        context = service.auth_service.build_authorizer().authorize(
            required_role="viewer",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id=None,
        )

        self.assertEqual(issue.response.user.email, "operator@example.com")
        self.assertEqual(issue.response.active_organization.slug, "acme")
        self.assertEqual(context.organization_id, "org-1")
        self.assertEqual(context.user_id, "user-operator-1")
        self.assertEqual(context.role, "operator")
        self.assertIsNotNone(context.session_id)

    def test_authorizer_rejects_insufficient_role_from_session_token(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "viewer@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )

        with self.assertRaises(OpsGraphAuthorizationError) as context:
            service.auth_service.build_authorizer().authorize(
                required_role="operator",
                authorization=f"Bearer {issue.response.access_token}",
                organization_id="org-1",
            )

        self.assertEqual(context.exception.code, "AUTH_FORBIDDEN")
        self.assertEqual(context.exception.status_code, 403)

    def test_refresh_session_rotates_refresh_token_and_revoke_invalidates_access(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        context = service.auth_service.build_authorizer().authorize(
            required_role="product_admin",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id="org-1",
        )
        refreshed = service.auth_service.refresh_session(issue.refresh_token)

        self.assertNotEqual(refreshed.refresh_token, issue.refresh_token)
        self.assertNotEqual(refreshed.response.access_token, issue.response.access_token)

        service.auth_service.revoke_session(context.session_id)

        with self.assertRaises(OpsGraphAuthorizationError) as revoked_context:
            service.auth_service.build_authorizer().authorize(
                required_role="viewer",
                authorization=f"Bearer {issue.response.access_token}",
                organization_id="org-1",
            )

        self.assertEqual(revoked_context.exception.code, "AUTH_SESSION_REVOKED")

    def test_get_current_user_returns_memberships(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        issue = service.auth_service.create_session(
            {
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            }
        )
        context = service.auth_service.build_authorizer().authorize(
            required_role="viewer",
            authorization=f"Bearer {issue.response.access_token}",
            organization_id=None,
        )
        current_user = service.auth_service.get_current_user(context)

        self.assertEqual(current_user.user.display_name, "Ops Admin")
        self.assertEqual(current_user.active_organization.organization_id, "org-1")
        self.assertEqual(current_user.memberships[0].role, "org_admin")

    def test_get_current_user_rejects_header_only_fallback_context(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        context = service.auth_service.build_authorizer().authorize(
            required_role="viewer",
            authorization="Bearer test-token",
            organization_id="org-1",
            user_id="user-1",
            user_role="viewer",
        )

        with self.assertRaises(OpsGraphAuthorizationError) as auth_error:
            service.auth_service.get_current_user(context)

        self.assertEqual(auth_error.exception.code, "AUTH_SESSION_REQUIRED")

    def test_membership_provision_and_role_update_revoke_existing_sessions(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        membership = service.auth_service.provision_membership(
            "org-1",
            {
                "email": "analyst@example.com",
                "display_name": "Ops Analyst",
                "role": "viewer",
                "password": "opsgraph-member-demo",
            },
            actor_user_id="user-admin-1",
        )
        issued = service.auth_service.create_session(
            {
                "email": "analyst@example.com",
                "password": "opsgraph-member-demo",
                "organization_slug": "acme",
            }
        )
        updated = service.auth_service.update_membership(
            "org-1",
            membership.membership_id,
            {
                "role": "operator",
            },
            actor_user_id="user-admin-1",
        )

        self.assertEqual(membership.role, "viewer")
        self.assertEqual(updated.role, "operator")

        with self.assertRaises(OpsGraphAuthorizationError) as revoked_context:
            service.auth_service.build_authorizer().authorize(
                required_role="viewer",
                authorization=f"Bearer {issued.response.access_token}",
                organization_id="org-1",
            )

        self.assertEqual(revoked_context.exception.code, "AUTH_SESSION_REVOKED")

    def test_membership_update_rejects_self_lockout(self) -> None:
        service = build_app_service()
        self.addCleanup(service.close)

        memberships = service.auth_service.list_memberships("org-1")
        admin_membership = next(item for item in memberships if item.user.user_id == "user-admin-1")

        with self.assertRaises(OpsGraphAuthorizationError) as auth_error:
            service.auth_service.update_membership(
                "org-1",
                admin_membership.membership_id,
                {
                    "role": "viewer",
                },
                actor_user_id="user-admin-1",
            )

        self.assertEqual(auth_error.exception.code, "AUTH_SELF_LOCKOUT_FORBIDDEN")


@unittest.skipIf(TestClient is None, "fastapi test client unavailable")
class OpsGraphRouteAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _AP.create_managed_test_client(self, create_fastapi_app(_stub_service()))

    def test_health_route_remains_public(self) -> None:
        response = self.client.get("/health")

        _AP.assert_health_response(self, response, product="opsgraph")

    def test_runtime_capabilities_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime-capabilities",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_runtime_capabilities_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime-capabilities",
            role="org_admin",
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "product": "opsgraph",
                "auth.mode": "demo_compatible",
                "auth.header_fallback_enabled": True,
                "replay_worker_alert_policy.warning_consecutive_failures": 1,
                "replay_worker_alert_policy.critical_consecutive_failures": 3,
            },
        )

    def test_remote_provider_smoke_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            json={},
        )

    def test_remote_provider_smoke_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            role="org_admin",
            json={"providers": ["deployment_lookup"]},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "diagnostic_run_id": "runtime-smoke-stub-1",
                "providers.0": "deployment_lookup",
                "summary.success_count": 0,
                "summary.skipped_count": 1,
                "summary.failed_count": 0,
                "results.0.provider": "deployment_lookup",
                "results.0.status": "skipped",
            },
        )

    def test_remote_provider_smoke_history_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_remote_provider_smoke_history_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
            role="org_admin",
            params={"limit": 1},
        )

        _AP.assert_fields(
            self,
            data[0],
            expected_fields={
                "diagnostic_run_id": "runtime-smoke-stub-1",
                "actor_role": "product_admin",
                "request_payload.providers.0": "deployment_lookup",
                "response.summary.skipped_count": 1,
            },
        )

    def test_remote_provider_smoke_history_route_supports_filters(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
            role="org_admin",
            params={
                "limit": 1,
                "actor_user_id": "user-admin-1",
                "request_id": "req-runtime-smoke-stub-1",
                "provider": "deployment_lookup",
            },
        )

        _AP.assert_collection_size(self, data, size=1)
        _AP.assert_fields(
            self,
            data[0],
            expected_fields={
                "diagnostic_run_id": "runtime-smoke-stub-1",
                "request_id": "req-runtime-smoke-stub-1",
                "response.results.0.provider": "deployment_lookup",
            },
        )

    def test_remote_provider_smoke_summary_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-summary",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_remote_provider_smoke_summary_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-summary",
            role="org_admin",
            params={"limit": 10, "provider": "deployment_lookup"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "scanned_run_count": 1,
                "provider_count": 1,
                "providers.0.provider": "deployment_lookup",
                "providers.0.last_status": "skipped",
            },
        )

    def test_replay_worker_alert_policy_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
        )

    def test_replay_worker_alert_policy_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "warning_consecutive_failures": 1,
                "critical_consecutive_failures": 3,
                "source": "default",
            },
        )

    def test_update_replay_worker_alert_policy_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
        )

    def test_update_replay_worker_alert_policy_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
                "source": "workspace_override",
            },
        )

    def test_replay_worker_monitor_presets_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-presets",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
        )

    def test_replay_worker_monitor_presets_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-presets",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
        )

        _AP.assert_collection_contains(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "policy_audit_copy_format": "markdown",
                "default_source": "shift_default",
            },
        )

    def test_replay_worker_monitor_shift_schedule_routes_allow_product_admin_alias(self) -> None:
        _get_response, get_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-shift-schedule",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
        )
        _put_response, put_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-shift-schedule",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
            json={
                "timezone": "UTC",
                "windows": [
                    {"shift_label": "day", "start_time": "08:00", "end_time": "20:00"},
                ],
                "date_overrides": [
                    {
                        "date": "2026-03-27",
                        "note": "Holiday",
                        "windows": [
                            {"shift_label": "holiday", "start_time": "10:00", "end_time": "14:00"},
                        ],
                    }
                ],
                "date_range_overrides": [
                    {
                        "start_date": "2026-03-28",
                        "end_date": "2026-03-30",
                        "note": "Migration week",
                        "windows": [
                            {"shift_label": "migration", "start_time": "09:00", "end_time": "18:00"},
                        ],
                    }
                ],
            },
        )
        _resolve_response, resolve_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-resolved-shift",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "at": "2026-03-27T11:00:00Z"},
        )
        _delete_response, delete_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "DELETE",
            "/api/v1/opsgraph/replays/worker-monitor-shift-schedule",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            get_data,
            expected_fields={"workspace_id": "ops-ws-1", "timezone": "UTC"},
        )
        _AP.assert_fields(
            self,
            put_data,
            expected_fields={"workspace_id": "ops-ws-1", "timezone": "UTC"},
        )
        self.assertEqual(put_data["date_overrides"][0]["date"], "2026-03-27")
        self.assertEqual(put_data["date_range_overrides"][0]["start_date"], "2026-03-28")
        _AP.assert_fields(
            self,
            resolve_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "shift_label": "holiday",
                "source": "date_override",
            },
        )
        _AP.assert_fields(
            self,
            delete_data,
            expected_fields={"workspace_id": "ops-ws-1", "cleared": True},
        )

    def test_replay_worker_monitor_shift_schedule_routes_require_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-shift-schedule",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
            json={
                "timezone": "UTC",
                "windows": [
                    {"shift_label": "day", "start_time": "08:00", "end_time": "20:00"},
                ],
            },
        )

    def test_upsert_and_delete_replay_worker_monitor_preset_routes_allow_product_admin_alias(self) -> None:
        _upsert_response, upsert_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
            json={
                "history_limit": 12,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-preset-1",
                "policy_audit_limit": 10,
                "policy_audit_copy_format": "slack",
                "policy_audit_include_summary": False,
            },
        )
        _delete_response, delete_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "DELETE",
            "/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            role="org_admin",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            upsert_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "history_limit": 12,
                "policy_audit_copy_format": "slack",
                "policy_audit_include_summary": False,
            },
        )
        _AP.assert_fields(
            self,
            delete_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "deleted": True,
            },
        )

    def test_set_and_clear_replay_worker_monitor_default_preset_routes_allow_product_admin_alias(self) -> None:
        _set_response, set_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-default-preset/night-shift",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
        )
        _clear_response, clear_data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "DELETE",
            "/api/v1/opsgraph/replays/worker-monitor-default-preset",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
        )

        _AP.assert_fields(
            self,
            set_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "shift_label": "night",
                "source": "shift_default",
                "cleared": False,
            },
        )
        _AP.assert_fields(
            self,
            clear_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "shift_label": "night",
                "source": "shift_default",
                "cleared": True,
            },
        )

    def test_replay_worker_monitor_default_preset_routes_require_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-default-preset/night-shift",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
        )

    def test_replay_admin_audit_logs_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/audit-logs",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
        )

    def test_replay_admin_audit_logs_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/audit-logs",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "action_type": "replay.update_worker_alert_policy"},
        )

        _AP.assert_collection_contains(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "action_type": "replay.update_worker_alert_policy",
            },
        )

    def test_replay_worker_status_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-status",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_replay_worker_status_route_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-status",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "history_limit": 2},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "current.status": "idle",
                "policy.source": "default",
            },
        )
        _AP.assert_collection_contains(
            self,
            data["history"],
            expected_fields={"status": "idle"},
        )

    def test_replay_worker_status_stream_route_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-status/stream",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_replay_worker_status_stream_route_allows_product_admin_alias(self) -> None:
        response = _AP.request_with_header_auth(
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-status/stream",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "history_limit": 2, "once": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        self.assertIn("event: opsgraph.replay_worker.status", response.text)
        self.assertIn("replay-worker:ops-ws-1", response.text)

    def test_replay_worker_monitor_page_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/opsgraph/replays/worker-monitor",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_replay_worker_monitor_page_allows_product_admin_alias(self) -> None:
        response = _AP.request_with_header_auth(
            self.client,
            "GET",
            "/opsgraph/replays/worker-monitor",
            role="org_admin",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("OpsGraph Replay Worker Monitor", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-status", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-status/stream", response.text)
        self.assertIn("/api/v1/opsgraph/runtime-capabilities", response.text)
        self.assertIn("EventSource", response.text)
        self.assertIn("Latest Failure", response.text)
        self.assertIn("No recent worker failure recorded.", response.text)
        self.assertIn("Remote Provider Smoke", response.text)
        self.assertIn("No remote provider smoke regression recorded.", response.text)
        self.assertIn("refreshRuntimeCapabilities", response.text)
        self.assertIn("Focus Alert Provider", response.text)
        self.assertIn("Remote Smoke Drilldown", response.text)
        self.assertIn("Provider Summary", response.text)
        self.assertIn("Recent Diagnostic Runs", response.text)
        self.assertIn("Refresh Smoke", response.text)
        self.assertIn("Clear Provider", response.text)
        self.assertIn("Copy Format", response.text)
        self.assertIn("smokeCopyFormat", response.text)
        self.assertIn("Copy Smoke Window", response.text)
        self.assertIn("Export Smoke Window", response.text)
        self.assertIn("Run Smoke", response.text)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke", response.text)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke-summary", response.text)
        self.assertIn("/api/v1/opsgraph/runtime/remote-provider-smoke-runs", response.text)
        self.assertIn("refreshSmokeDrilldown", response.text)
        self.assertIn("refreshRuntimeSignals", response.text)
        self.assertIn("runRemoteProviderSmokeForProvider", response.text)
        self.assertIn("describeSmokeRunResult", response.text)
        self.assertIn("Show Details", response.text)
        self.assertIn("Copy Context", response.text)
        self.assertIn("Export JSON", response.text)
        self.assertIn("Response Summary", response.text)
        self.assertIn("Result Details", response.text)
        self.assertIn("toggleSmokeRunDetails", response.text)
        self.assertIn("buildSmokeRunDetailPayload", response.text)
        self.assertIn("copySmokeRunContext", response.text)
        self.assertIn("exportSmokeRunDetails", response.text)
        self.assertIn("copySmokeWindowContext", response.text)
        self.assertIn("exportSmokeWindow", response.text)
        self.assertIn("getSmokeCopyFormat", response.text)
        self.assertIn("smoke_copy_format", response.text)
        self.assertIn("buildSmokeWindowContextText", response.text)
        self.assertIn("buildSmokeRunContextText", response.text)
        self.assertIn("buildSmokeResultDigestLines", response.text)
        self.assertIn("buildSmokeProviderDigestLines", response.text)
        self.assertIn("buildSmokeRunDigestLines", response.text)
        self.assertIn("buildSmokeRunExportPayload", response.text)
        self.assertIn("buildSmokeRunExportFilename", response.text)
        self.assertIn("buildSmokeWindowExportPayload", response.text)
        self.assertIn("buildSmokeWindowExportFilename", response.text)
        self.assertIn("renderSmokeSummary", response.text)
        self.assertIn("renderSmokeRuns", response.text)
        self.assertIn("startRuntimeSignalRefreshLoop", response.text)
        self.assertIn("stopRuntimeSignalRefreshLoop", response.text)
        self.assertIn("setInterval", response.text)
        self.assertIn("pagehide", response.text)
        self.assertIn("Live via SSE + smoke polling", response.text)
        self.assertIn("Alert Policy", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-alert-policy", response.text)
        self.assertIn("Reset to Default", response.text)
        self.assertIn("Shift Schedule", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-shift-schedule", response.text)
        self.assertIn("Base Windows JSON", response.text)
        self.assertIn("Date Overrides JSON", response.text)
        self.assertIn("Range Overrides JSON", response.text)
        self.assertIn("Structured Editor", response.text)
        self.assertIn("Advanced JSON", response.text)
        self.assertIn("Use Edit to pull a row back into the quick form.", response.text)
        self.assertIn("Base Window Label", response.text)
        self.assertIn("Add Base Window", response.text)
        self.assertIn("Add Date Override Window", response.text)
        self.assertIn("Add Range Override Window", response.text)
        self.assertIn("No base windows configured.", response.text)
        self.assertIn("No date overrides configured.", response.text)
        self.assertIn("No range overrides configured.", response.text)
        self.assertIn("Load Schedule", response.text)
        self.assertIn("Save Schedule", response.text)
        self.assertIn("Clear Schedule", response.text)
        self.assertIn("Copy Schedule JSON", response.text)
        self.assertIn("Export Schedule JSON", response.text)
        self.assertIn("Import Schedule JSON", response.text)
        self.assertIn("Import Preview", response.text)
        self.assertIn("Apply Import to Draft", response.text)
        self.assertIn("Discard Import Preview", response.text)
        self.assertIn("No import preview available.", response.text)
        self.assertIn("Detailed Window Diff", response.text)
        self.assertIn("No detailed import diff available.", response.text)
        self.assertIn("Recent Policy Changes", response.text)
        self.assertIn("/api/v1/opsgraph/replays/audit-logs", response.text)
        self.assertIn("Apply Filters", response.text)
        self.assertIn("Clear Filters", response.text)
        self.assertIn("Copy Filter Link", response.text)
        self.assertIn("Copy Latest Context", response.text)
        self.assertIn("Copy Format", response.text)
        self.assertIn("Markdown", response.text)
        self.assertIn("Slack", response.text)
        self.assertIn("Include Monitor Summary", response.text)
        self.assertIn("Preset Name", response.text)
        self.assertIn("Saved Presets", response.text)
        self.assertIn("Preset Scope", response.text)
        self.assertIn("Shift Source", response.text)
        self.assertIn("Shift Label", response.text)
        self.assertIn("Auto", response.text)
        self.assertIn("Workspace", response.text)
        self.assertIn("Browser", response.text)
        self.assertIn("Save Preset", response.text)
        self.assertIn("Load Preset", response.text)
        self.assertIn("Delete Preset", response.text)
        self.assertIn("Set Workspace Default", response.text)
        self.assertIn("Clear Default", response.text)
        self.assertIn("Export JSON", response.text)
        self.assertIn("Export CSV", response.text)
        self.assertIn("Export Latest JSON", response.text)
        self.assertIn("Export Latest CSV", response.text)
        self.assertIn("Load Older", response.text)
        self.assertIn("Newest First", response.text)
        self.assertIn("Copy Request", response.text)
        self.assertIn("Row Context", response.text)
        self.assertIn("Show Payload", response.text)
        self.assertIn("Use Filters", response.text)
        self.assertIn("Row JSON", response.text)
        self.assertIn("Row CSV", response.text)
        self.assertIn("exportPolicyAuditWindow", response.text)
        self.assertIn("exportLatestPolicyAudit", response.text)
        self.assertIn("exportSinglePolicyAudit", response.text)
        self.assertIn("buildPolicyAuditExportFilename", response.text)
        self.assertIn("copyLatestPolicyAuditContext", response.text)
        self.assertIn("copyPolicyAuditContext", response.text)
        self.assertIn("copySinglePolicyAuditContext", response.text)
        self.assertIn("getPolicyAuditCopyFormat", response.text)
        self.assertIn("buildPolicyAuditContextText", response.text)
        self.assertIn("buildMonitorAbsoluteUrl", response.text)
        self.assertIn("buildPolicyAuditMonitorSummary", response.text)
        self.assertIn("buildPolicyAuditPresetSnapshot", response.text)
        self.assertIn("saveCurrentPolicyAuditPreset", response.text)
        self.assertIn("loadSelectedPolicyAuditPreset", response.text)
        self.assertIn("deleteSelectedPolicyAuditPreset", response.text)
        self.assertIn("setSelectedPolicyAuditDefaultPreset", response.text)
        self.assertIn("clearSelectedPolicyAuditDefaultPreset", response.text)
        self.assertIn("fetchWorkspacePolicyAuditPresets", response.text)
        self.assertIn("refreshPolicyAuditShiftResolution", response.text)
        self.assertIn("setWorkspacePolicyAuditDefaultPreset", response.text)
        self.assertIn("clearWorkspacePolicyAuditDefaultPreset", response.text)
        self.assertIn("upsertWorkspacePolicyAuditPreset", response.text)
        self.assertIn("deleteWorkspacePolicyAuditPreset", response.text)
        self.assertIn("fetchReplayWorkerMonitorShiftSchedule", response.text)
        self.assertIn("refreshShiftScheduleEditor", response.text)
        self.assertIn("syncShiftScheduleDraftFromEditors", response.text)
        self.assertIn("saveShiftSchedule", response.text)
        self.assertIn("clearShiftSchedule", response.text)
        self.assertIn("copyShiftScheduleJson", response.text)
        self.assertIn("exportShiftScheduleJson", response.text)
        self.assertIn("promptShiftScheduleJsonImport", response.text)
        self.assertIn("importShiftScheduleJson", response.text)
        self.assertIn("normalizeImportedShiftSchedule", response.text)
        self.assertIn("buildShiftScheduleExportFilename", response.text)
        self.assertIn("renderShiftScheduleImportPreview", response.text)
        self.assertIn("clearShiftScheduleImportPreview", response.text)
        self.assertIn("applyShiftScheduleImportPreview", response.text)
        self.assertIn("discardShiftScheduleImportPreview", response.text)
        self.assertIn("buildShiftScheduleComparisonEntries", response.text)
        self.assertIn("buildShiftScheduleOrderComparisons", response.text)
        self.assertIn("buildShiftScheduleImportDetailRows", response.text)
        self.assertIn("reordered", response.text)
        self.assertIn("addShiftScheduleBaseWindow", response.text)
        self.assertIn("addShiftScheduleDateOverrideWindow", response.text)
        self.assertIn("addShiftScheduleRangeOverrideWindow", response.text)
        self.assertIn("editShiftScheduleBaseWindow", response.text)
        self.assertIn("editShiftScheduleDateOverrideWindow", response.text)
        self.assertIn("editShiftScheduleRangeOverrideWindow", response.text)
        self.assertIn("moveShiftScheduleBaseWindow", response.text)
        self.assertIn("moveShiftScheduleDateOverrideWindow", response.text)
        self.assertIn("moveShiftScheduleRangeOverrideWindow", response.text)
        self.assertIn("removeShiftScheduleBaseWindow", response.text)
        self.assertIn("removeShiftScheduleDateOverrideWindow", response.text)
        self.assertIn("removeShiftScheduleRangeOverrideWindow", response.text)
        self.assertIn("Loaded base window into form for editing.", response.text)
        self.assertIn("Loaded date override window into form for editing.", response.text)
        self.assertIn("Loaded range override window into form for editing.", response.text)
        self.assertIn("move-base-window-up", response.text)
        self.assertIn("move-date-override-window-up", response.text)
        self.assertIn("move-range-override-window-up", response.text)
        self.assertIn("opsgraph.replay_worker_monitor_presets.v1", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-presets", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-default-preset", response.text)
        self.assertIn("/api/v1/opsgraph/replays/worker-monitor-resolved-shift", response.text)
        self.assertIn("shiftScheduleDraftMeta", response.text)
        self.assertIn("shiftScheduleBaseLabel", response.text)
        self.assertIn("shiftScheduleBaseStart", response.text)
        self.assertIn("shiftScheduleBaseEnd", response.text)
        self.assertIn("shiftScheduleDateOverrideDate", response.text)
        self.assertIn("shiftScheduleDateOverrideNote", response.text)
        self.assertIn("shiftScheduleDateOverrideLabel", response.text)
        self.assertIn("shiftScheduleDateOverrideStart", response.text)
        self.assertIn("shiftScheduleDateOverrideEnd", response.text)
        self.assertIn("shiftScheduleRangeOverrideStartDate", response.text)
        self.assertIn("shiftScheduleRangeOverrideEndDate", response.text)
        self.assertIn("shiftScheduleRangeOverrideNote", response.text)
        self.assertIn("shiftScheduleRangeOverrideLabel", response.text)
        self.assertIn("shiftScheduleRangeOverrideStart", response.text)
        self.assertIn("shiftScheduleRangeOverrideEnd", response.text)
        self.assertIn("shiftScheduleWindowsBody", response.text)
        self.assertIn("shiftScheduleDateOverridesBody", response.text)
        self.assertIn("shiftScheduleDateRangeOverridesBody", response.text)
        self.assertIn("shiftScheduleImportInput", response.text)
        self.assertIn("shiftScheduleImportPreviewPanel", response.text)
        self.assertIn("shiftScheduleImportPreviewMeta", response.text)
        self.assertIn("shiftScheduleImportPreviewText", response.text)
        self.assertIn("shiftScheduleImportPreviewBody", response.text)
        self.assertIn("shiftScheduleImportDetailBody", response.text)
        self.assertIn("applyShiftScheduleImportButton", response.text)
        self.assertIn("discardShiftScheduleImportButton", response.text)
        self.assertIn("shiftScheduleTimezone", response.text)
        self.assertIn("shiftScheduleWindows", response.text)
        self.assertIn("shiftScheduleDateOverrides", response.text)
        self.assertIn("shiftScheduleDateRangeOverrides", response.text)
        self.assertIn("shiftScheduleMeta", response.text)
        self.assertIn("shiftScheduleActionStatus", response.text)
        self.assertIn("policyAuditCopyFormat", response.text)
        self.assertIn("policyAuditIncludeSummary", response.text)
        self.assertIn("policyAuditPresetScope", response.text)
        self.assertIn("policyAuditShiftSource", response.text)
        self.assertIn("policyAuditShiftLabel", response.text)
        self.assertIn("policyAuditShiftMeta", response.text)
        self.assertIn("policyAuditPresetName", response.text)
        self.assertIn("policyAuditPresetSelect", response.text)
        self.assertIn("summary_copy_format", response.text)
        self.assertIn("summary_policy_audit_preset_scope", response.text)
        self.assertIn("summary_policy_audit_shift_source", response.text)
        self.assertIn("summary_policy_audit_shift_label", response.text)
        self.assertIn("summary_policy_audit_effective_shift_label", response.text)
        self.assertIn("summary_policy_audit_preset_name", response.text)
        self.assertIn("summary_policy_audit_preset_is_default", response.text)
        self.assertIn("summary_policy_audit_preset_default_source", response.text)
        self.assertIn("summary_policy_audit_resolved_shift_date", response.text)
        self.assertIn("summary_policy_audit_resolved_shift_range_start_date", response.text)
        self.assertIn("summary_policy_audit_resolved_shift_range_end_date", response.text)
        self.assertIn("summary_policy_audit_resolved_shift_note", response.text)
        self.assertIn("summary_monitor_absolute_url", response.text)
        self.assertIn("policy_audit_include_summary", response.text)
        self.assertIn("policy_audit_shift_label", response.text)
        self.assertIn("policyAuditLimit", response.text)
        self.assertIn("policyAuditRequest", response.text)
        self.assertIn("policyAuditActionStatus", response.text)
        self.assertIn("policy-audit-fresh", response.text)
        self.assertIn("fresh-flag", response.text)
        self.assertIn("audit-detail-row", response.text)
        self.assertIn("Request Payload", response.text)
        self.assertIn("Result Payload", response.text)
        self.assertIn("No replay worker policy changes recorded.", response.text)

    def test_viewer_route_requires_authorization_header(self) -> None:
        _AP.request_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/incidents",
            status_code=401,
            error_code="AUTH_REQUIRED",
            params={"workspace_id": "ops-ws-1"},
            headers={"X-Organization-Id": "org-1"},
        )

    def test_viewer_route_requires_organization_context(self) -> None:
        _AP.request_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/incidents",
            status_code=400,
            error_code="TENANT_CONTEXT_REQUIRED",
            params={"workspace_id": "ops-ws-1"},
            headers={"Authorization": "Bearer test-token"},
        )

    def test_viewer_route_allows_viewer_role(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/incidents",
            role="viewer",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_collection_contains(self, data, expected_fields={"id": "incident-1"})

    def test_operator_route_rejects_viewer_role(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/incidents/incident-1/facts",
            role="viewer",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            headers={
                "Idempotency-Key": "fact-create-1",
            },
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )

    def test_operator_route_allows_org_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/incidents/incident-1/facts",
            role="org_admin",
            headers={
                "Idempotency-Key": "fact-create-2",
            },
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )

        _AP.assert_fields(self, data, expected_fields={"fact_id": "fact-1"})

    def test_replay_trigger_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/run",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            headers={
                "Idempotency-Key": "replay-run-1",
            },
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "bundle-v1",
            },
        )

    def test_replay_trigger_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/run",
            role="org_admin",
            status_code=202,
            headers={
                "Idempotency-Key": "replay-run-2",
            },
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "bundle-v1",
            },
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={"status": "queued", "workflow_run_id": None},
        )

    def test_replay_batch_process_requires_product_admin_access(self) -> None:
        _AP.request_with_header_auth_and_assert_json_error(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/process-queued",
            role="operator",
            status_code=403,
            error_code="AUTH_FORBIDDEN",
            params={"workspace_id": "ops-ws-1"},
        )

    def test_replay_batch_process_allows_product_admin_alias(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/process-queued",
            role="org_admin",
            params={"workspace_id": "ops-ws-1", "limit": 2},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "processed_count": 1,
                "completed_count": 1,
                "remaining_queued_count": 0,
            },
        )

    def test_replay_quality_summary_route_allows_viewer_access(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/summary",
            role="viewer",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "baseline_count": 1,
                "evaluation_count": 1,
                "replay_pass_rate": 1.0,
                "avg_top_hypothesis_hit_rate": 1.0,
            },
        )


@unittest.skipIf(TestClient is None, "fastapi test client unavailable")
class OpsGraphAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = build_app_service()
        self.app = create_fastapi_app(self.service)
        _AP.assert_managed_app_service(self, self.app, state_attr="opsgraph_service")
        self.client = _AP.create_managed_test_client(self, self.app)

    def test_session_routes_issue_cookie_and_authorize_me(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="operator@example.com",
            password="opsgraph-demo",
        )

        _me_response, me_data = _AP.get_current_user_via_bearer(
            self,
            self.client,
            access_token=access_token,
        )

        _AP.assert_fields(
            self,
            me_data,
            expected_fields={"active_organization.slug": "acme"},
        )

    def test_header_auth_still_works_when_auth_service_is_enabled(self) -> None:
        _response, data = _AP.request_with_header_auth_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/incidents",
            role="viewer",
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_collection_contains(self, data, expected_fields={"id": "incident-1"})

    def test_refresh_and_revoke_current_session(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _refresh_response, refreshed_access_token = _AP.refresh_session_access_token(self, self.client)
        self.assertNotEqual(refreshed_access_token, access_token)

        _AP.revoke_current_session(self, self.client, access_token=refreshed_access_token)

        _AP.request_with_bearer_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/me",
            access_token=refreshed_access_token,
            status_code=401,
            error_code="AUTH_SESSION_REVOKED",
        )

    def test_session_admin_can_trigger_replay_route_and_read_audit_logs(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        replay_response, _replay_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/run",
            access_token=access_token,
            headers={"Idempotency-Key": "route-replay-session-1"},
            status_code=202,
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "route-bundle-v1",
            },
        )
        _audit_response, audit_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/incidents/incident-1/audit-logs",
            access_token=access_token,
            params={"action_type": "replay.start_run"},
        )

        _AP.assert_fields(
            self,
            _replay_data,
            expected_fields={"status": "queued", "workflow_run_id": None},
        )
        _AP.assert_collection_contains(
            self,
            audit_data,
            expected_fields={"action_type": "replay.start_run"},
        )

    def test_session_admin_can_process_queued_replays(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _start_response, start_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/run",
            access_token=access_token,
            headers={"Idempotency-Key": "route-replay-batch-1"},
            status_code=202,
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "route-batch-v1",
            },
        )
        _process_response, process_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/process-queued",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            process_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "queued_count": 1,
                "processed_count": 1,
                "completed_count": 1,
                "failed_count": 0,
                "skipped_count": 0,
                "remaining_queued_count": 0,
            },
        )
        _AP.assert_collection_contains(
            self,
            process_data["items"],
            expected_fields={"id": start_data["id"], "status": "completed"},
        )

    def test_session_admin_can_read_runtime_capabilities(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime-capabilities",
            access_token=access_token,
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "model_provider.effective_mode": "local",
                "tooling.incident_store.backend_id": "sqlalchemy-repository",
                "replay_worker_alert_policy.warning_consecutive_failures": 1,
            },
        )

    def test_session_admin_can_run_remote_provider_smoke(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            access_token=access_token,
            json={"providers": ["deployment_lookup"]},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "providers.0": "deployment_lookup",
                "summary.skipped_count": 1,
                "exit_code": 0,
            },
        )
        self.assertTrue(str(data["diagnostic_run_id"]).startswith("runtime-smoke-"))

    def test_session_admin_can_read_remote_provider_smoke_history(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            access_token=access_token,
            headers={"X-Request-Id": "req-runtime-smoke-history-1"},
            json={"providers": ["deployment_lookup"]},
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
            access_token=access_token,
            params={"limit": 1},
        )

        _AP.assert_fields(
            self,
            data[0],
            expected_fields={
                "actor_user_id": "user-admin-1",
                "request_id": "req-runtime-smoke-history-1",
                "request_payload.providers.0": "deployment_lookup",
                "response.summary.skipped_count": 1,
            },
        )

    def test_session_admin_can_filter_remote_provider_smoke_history(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            access_token=access_token,
            headers={"X-Request-Id": "req-runtime-smoke-filter-1"},
            json={"providers": ["deployment_lookup"]},
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            access_token=access_token,
            headers={"X-Request-Id": "req-runtime-smoke-filter-2"},
            json={"providers": ["runbook_search"]},
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-runs",
            access_token=access_token,
            params={
                "limit": 5,
                "actor_user_id": "user-admin-1",
                "request_id": "req-runtime-smoke-filter-2",
                "provider": "runbook_search",
            },
        )

        _AP.assert_collection_size(self, data, size=1)
        _AP.assert_fields(
            self,
            data[0],
            expected_fields={
                "actor_user_id": "user-admin-1",
                "request_id": "req-runtime-smoke-filter-2",
                "request_payload.providers.0": "runbook_search",
                "response.results.0.provider": "runbook_search",
            },
        )

    def test_session_admin_can_read_remote_provider_smoke_summary(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/opsgraph/runtime/remote-provider-smoke",
            access_token=access_token,
            headers={"X-Request-Id": "req-runtime-smoke-summary-1"},
            json={"providers": ["deployment_lookup"]},
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/runtime/remote-provider-smoke-summary",
            access_token=access_token,
            params={"limit": 5, "provider": "deployment_lookup"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "provider_count": 1,
                "providers.0.provider": "deployment_lookup",
                "providers.0.run_count": 1,
                "providers.0.last_status": "skipped",
            },
        )

    def test_session_admin_can_read_replay_worker_alert_policy_route(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "warning_consecutive_failures": 1,
                "critical_consecutive_failures": 3,
                "source": "default",
            },
        )

    def test_session_admin_can_update_replay_worker_alert_policy_route(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "warning_consecutive_failures": 2,
                "critical_consecutive_failures": 4,
                "source": "workspace_override",
            },
        )

    def test_session_admin_can_manage_replay_worker_monitor_presets(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _list_response, initial_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-presets",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
        )
        _upsert_response, upsert_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            headers={"X-Request-Id": "req-session-monitor-preset-1"},
            json={
                "history_limit": 12,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-1",
                "policy_audit_limit": 10,
                "policy_audit_copy_format": "markdown",
                "policy_audit_include_summary": True,
            },
        )
        _delete_response, delete_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "DELETE",
            "/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            headers={"X-Request-Id": "req-session-monitor-preset-2"},
        )

        _AP.assert_collection_size(self, initial_data, size=0)
        _AP.assert_fields(
            self,
            upsert_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "history_limit": 12,
                "policy_audit_copy_format": "markdown",
            },
        )
        _AP.assert_fields(
            self,
            delete_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "deleted": True,
            },
        )

    def test_session_admin_can_manage_replay_worker_monitor_default_preset(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-presets/night-shift",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={
                "history_limit": 12,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-1",
                "policy_audit_limit": 10,
                "policy_audit_copy_format": "markdown",
                "policy_audit_include_summary": True,
            },
        )
        _set_response, set_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PUT",
            "/api/v1/opsgraph/replays/worker-monitor-default-preset/night-shift",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
            headers={"X-Request-Id": "req-session-monitor-default-1"},
        )
        _list_response, list_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-monitor-presets",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
        )
        _clear_response, clear_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "DELETE",
            "/api/v1/opsgraph/replays/worker-monitor-default-preset",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1", "shift_label": "night"},
            headers={"X-Request-Id": "req-session-monitor-default-2"},
        )

        _AP.assert_fields(
            self,
            set_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "shift_label": "night",
                "source": "shift_default",
                "cleared": False,
            },
        )
        _AP.assert_collection_contains(
            self,
            list_data,
            expected_fields={
                "preset_name": "night-shift",
                "is_default": True,
                "default_source": "shift_default",
            },
        )
        _AP.assert_fields(
            self,
            clear_data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "preset_name": "night-shift",
                "shift_label": "night",
                "source": "shift_default",
                "cleared": True,
            },
        )

    def test_session_admin_can_read_replay_admin_audit_logs(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
            headers={"X-Request-Id": "req-policy-audit-1"},
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 3, "critical_consecutive_failures": 5},
            headers={"X-Request-Id": "req-policy-audit-2"},
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/audit-logs",
            access_token=access_token,
            params={
                "workspace_id": "ops-ws-1",
                "action_type": "replay.update_worker_alert_policy",
                "request_id": "req-policy-audit-1",
            },
        )

        _AP.assert_collection_size(self, data, size=1)
        _AP.assert_collection_contains(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "action_type": "replay.update_worker_alert_policy",
                "subject_type": "replay_worker_alert_policy",
                "request_id": "req-policy-audit-1",
            },
        )

    def test_session_admin_can_read_replay_quality_summary(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        baseline_response = _AP.request_with_bearer(
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/baselines/capture",
            access_token=access_token,
            json={"incident_id": "incident-1", "model_bundle_version": "route-summary-v1"},
        )
        self.assertEqual(baseline_response.status_code, 200)
        baseline_id = baseline_response.json()["data"]["baseline_id"]
        replay_response = _AP.request_with_bearer(
            self.client,
            "POST",
            "/api/v1/opsgraph/replays/run",
            access_token=access_token,
            headers={"Idempotency-Key": "route-summary-replay-1"},
            json={"incident_id": "incident-1", "model_bundle_version": "route-summary-v1"},
        )
        self.assertEqual(replay_response.status_code, 202)
        replay_run_id = replay_response.json()["data"]["id"]
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            f"/api/v1/opsgraph/replays/{replay_run_id}/execute",
            access_token=access_token,
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            f"/api/v1/opsgraph/replays/{replay_run_id}/evaluate",
            access_token=access_token,
            json={"baseline_id": baseline_id},
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/summary",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "baseline_count": 1,
                "evaluation_count": 1,
                "mismatched_evaluation_count": 1,
                "replay_pass_rate": 0.0,
            },
        )

    def test_session_admin_can_page_replay_admin_audit_logs(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
            headers={"X-Request-Id": "req-policy-page-1"},
        )
        _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            "/api/v1/opsgraph/replays/worker-alert-policy",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1"},
            json={"warning_consecutive_failures": 3, "critical_consecutive_failures": 5},
            headers={"X-Request-Id": "req-policy-page-2"},
        )

        response = _AP.request_with_bearer(
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/audit-logs",
            access_token=access_token,
            params={
                "workspace_id": "ops-ws-1",
                "action_type": "replay.update_worker_alert_policy",
                "limit": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        first_payload = response.json()
        _AP.assert_collection_size(self, first_payload["data"], size=1)
        self.assertTrue(first_payload["meta"]["has_more"])
        self.assertIsNotNone(first_payload["meta"].get("next_cursor"))
        self.assertEqual(first_payload["data"][0]["request_id"], "req-policy-page-2")

        second_response = _AP.request_with_bearer(
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/audit-logs",
            access_token=access_token,
            params={
                "workspace_id": "ops-ws-1",
                "action_type": "replay.update_worker_alert_policy",
                "limit": 1,
                "cursor": first_payload["meta"]["next_cursor"],
            },
        )

        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.json()
        _AP.assert_collection_size(self, second_payload["data"], size=1)
        self.assertFalse(second_payload["meta"]["has_more"])
        self.assertEqual(second_payload["data"][0]["request_id"], "req-policy-page-1")

    def test_session_admin_can_read_replay_worker_status_route(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        worker = OpsGraphReplayWorker(self.service)
        self.service.start_replay_run(
            {
                "incident_id": "incident-1",
                "replay_case_id": None,
                "model_bundle_version": "route-worker-status-v1",
            },
            idempotency_key="route-worker-status-1",
        )
        worker.build_supervisor().run(
            poll_interval_seconds=0,
            max_iterations=2,
            max_idle_polls=1,
            heartbeat_every_iterations=1,
        )

        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/opsgraph/replays/worker-status",
            access_token=access_token,
            params={"workspace_id": "ops-ws-1", "history_limit": 2},
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "workspace_id": "ops-ws-1",
                "current.status": "idle",
                "current.remaining_queued_count": 0,
                "policy.workspace_id": "ops-ws-1",
            },
        )
        _AP.assert_collection_size(self, data["history"], size=2)
        _AP.assert_collection_contains(
            self,
            data["history"],
            expected_fields={"status": "active"},
        )

    def test_session_admin_can_open_replay_worker_monitor_page(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        response = _AP.request_with_bearer(
            self.client,
            "GET",
            "/opsgraph/replays/worker-monitor",
            access_token=access_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("Refresh Now", response.text)

    def test_session_admin_can_stream_replay_worker_status(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        worker = OpsGraphReplayWorker(self.service)
        self.service.start_replay_run(
            {
                "incident_id": "incident-1",
                "replay_case_id": None,
                "model_bundle_version": "route-worker-stream-v1",
            },
            idempotency_key="route-worker-stream-1",
        )
        worker.build_supervisor().run(
            poll_interval_seconds=0,
            max_iterations=2,
            max_idle_polls=1,
            heartbeat_every_iterations=1,
        )

        response = self.client.get(
            "/api/v1/opsgraph/replays/worker-status/stream",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"workspace_id": "ops-ws-1", "history_limit": 2, "once": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        self.assertIn("event: opsgraph.replay_worker.status", response.text)
        self.assertIn("\"workspace_id\": \"ops-ws-1\"", response.text)

    def test_membership_admin_routes_require_product_admin_access(self) -> None:
        _login_response, access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="operator@example.com",
            password="opsgraph-demo",
        )

        _AP.request_with_bearer_and_assert_json_error(
            self,
            self.client,
            "GET",
            "/api/v1/auth/memberships",
            access_token=access_token,
            status_code=403,
            error_code="AUTH_FORBIDDEN",
        )

    def test_membership_admin_routes_provision_update_and_suspend_member(self) -> None:
        _admin_login, admin_access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )

        _list_response, list_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/auth/memberships",
            access_token=admin_access_token,
        )
        _create_response, create_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "POST",
            "/api/v1/auth/memberships",
            access_token=admin_access_token,
            json={
                "email": "member-route@example.com",
                "display_name": "Member Route",
                "role": "viewer",
                "password": "opsgraph-route-member",
            },
        )

        _AP.assert_collection_size(self, list_data, min_size=1)
        membership_id = create_data["id"]
        _AP.assert_fields(self, create_data, expected_fields={"role": "viewer"})

        _member_login, member_access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="member-route@example.com",
            password="opsgraph-route-member",
        )

        _promote_response, promote_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            f"/api/v1/auth/memberships/{membership_id}",
            access_token=admin_access_token,
            json={"role": "operator"},
        )
        revoked_member_response = _AP.request_with_bearer(
            self.client,
            "GET",
            "/api/v1/me",
            access_token=member_access_token,
        )

        _AP.assert_fields(self, promote_data, expected_fields={"role": "operator"})
        _AP.assert_json_error_response(
            self,
            revoked_member_response,
            status_code=401,
            error_code="AUTH_SESSION_REVOKED",
        )

        _promoted_login, promoted_access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="member-route@example.com",
            password="opsgraph-route-member",
        )

        operator_action = _AP.request_with_bearer(
            self.client,
            "POST",
            "/api/v1/opsgraph/incidents/incident-1/facts",
            access_token=promoted_access_token,
            headers={"Idempotency-Key": "route-member-fact-1"},
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )
        _suspend_response, suspend_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "PATCH",
            f"/api/v1/auth/memberships/{membership_id}",
            access_token=admin_access_token,
            json={"status": "suspended"},
        )
        suspended_member_response = _AP.request_with_bearer(
            self.client,
            "GET",
            "/api/v1/me",
            access_token=promoted_access_token,
        )
        blocked_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "member-route@example.com",
                "password": "opsgraph-route-member",
                "organization_slug": "acme",
            },
        )

        _AP.assert_json_data_response(self, operator_action, status_code=200)
        _AP.assert_fields(self, suspend_data, expected_fields={"status": "suspended"})
        _AP.assert_json_error_response(
            self,
            suspended_member_response,
            status_code=401,
            error_code="AUTH_SESSION_REVOKED",
        )
        _AP.assert_json_error_response(
            self,
            blocked_login,
            status_code=401,
            error_code="AUTH_INVALID_CREDENTIALS",
        )

    def test_membership_admin_route_prevents_self_lockout(self) -> None:
        _admin_login, admin_access_token = _AP.login_via_session_route(
            self,
            self.client,
            email="admin@example.com",
            password="opsgraph-demo",
        )
        _memberships_response, memberships_data = _AP.request_with_bearer_and_get_data(
            self,
            self.client,
            "GET",
            "/api/v1/auth/memberships",
            access_token=admin_access_token,
        )
        admin_membership = _AP.assert_collection_contains(
            self,
            memberships_data,
            expected_fields={"user.email": "admin@example.com"},
        )

        _AP.request_with_bearer_and_assert_json_error(
            self,
            self.client,
            "PATCH",
            f"/api/v1/auth/memberships/{admin_membership['id']}",
            access_token=admin_access_token,
            status_code=409,
            error_code="AUTH_SELF_LOCKOUT_FORBIDDEN",
            json={"role": "viewer"},
        )


@unittest.skipIf(TestClient is None, "fastapi test client unavailable")
class OpsGraphPersistentAuthRouteTests(unittest.TestCase):
    def test_persistent_business_routes_reject_header_only_auth_by_default(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env():
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)
        client = _AP.create_managed_test_client(self, create_fastapi_app(service))

        _AP.request_with_header_auth_and_assert_json_error(
            self,
            client,
            "GET",
            "/api/v1/opsgraph/incidents",
            role="viewer",
            params={"workspace_id": "ops-ws-1"},
            status_code=401,
            error_code="AUTH_SESSION_REQUIRED",
        )

    def test_persistent_auth_routes_accept_bootstrap_admin_session(self) -> None:
        temp_dir, database_url = _create_auth_database_url()
        self.addCleanup(temp_dir.cleanup)
        with _patched_auth_env(
            OPSGRAPH_BOOTSTRAP_ADMIN_EMAIL="bootstrap-admin@example.com",
            OPSGRAPH_BOOTSTRAP_ADMIN_PASSWORD="bootstrap-secret",
            OPSGRAPH_BOOTSTRAP_ORG_SLUG="bootstrap-org",
        ):
            service = build_app_service(database_url=database_url)
        self.addCleanup(service.close)
        client = _AP.create_managed_test_client(self, create_fastapi_app(service))

        _login_response, access_token = _AP.login_via_session_route(
            self,
            client,
            email="bootstrap-admin@example.com",
            password="bootstrap-secret",
            organization_slug="bootstrap-org",
        )
        _response, data = _AP.request_with_bearer_and_get_data(
            self,
            client,
            "GET",
            "/api/v1/opsgraph/runtime-capabilities",
            access_token=access_token,
        )

        _AP.assert_fields(
            self,
            data,
            expected_fields={
                "product": "opsgraph",
                "auth.mode": "strict",
                "auth.header_fallback_enabled": False,
                "replay_worker_alert_policy.warning_consecutive_failures": 1,
                "replay_worker_alert_policy.critical_consecutive_failures": 3,
            },
        )


def _stub_service():
    return SimpleNamespace(
        auth_service=None,
        get_health_status=lambda: {
            "status": "ok",
            "product": "opsgraph",
            "runtime_summary": {
                "model_provider_mode": "local",
                "model_backend_id": "heuristic-local",
                "tooling_profile": "product-runtime",
                "auth_mode": "demo_compatible",
                "auth_header_fallback_enabled": True,
                "auth_demo_seed_enabled": True,
                "auth_bootstrap_admin_configured": False,
                "replay_worker_alert_level": "healthy",
            },
        },
        get_runtime_capabilities=lambda: {
            "product": "opsgraph",
            "model_provider": {
                "requested_mode": "auto",
                "effective_mode": "local",
                "backend_id": "heuristic-local",
                "fallback_reason": "MODEL_PROVIDER_NOT_CONFIGURED",
                "details": {},
            },
            "auth": {
                "mode": "demo_compatible",
                "header_fallback_enabled": True,
                "demo_seed_enabled": True,
                "bootstrap_admin_configured": False,
                "bootstrap_organization_slug": None,
            },
            "tooling": {
                "incident_store": {
                    "requested_mode": "local",
                    "effective_mode": "local",
                    "backend_id": "sqlalchemy-repository",
                    "fallback_reason": None,
                    "details": {},
                }
            },
            "replay_worker": {
                "workspace_id": "ops-ws-1",
                "status": "idle",
                "iteration": 2,
                "attempted_count": 0,
                "dispatched_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "idle_polls": 1,
                "consecutive_failures": 0,
                "remaining_queued_count": 0,
                "error_message": None,
                "last_seen_at": "2026-03-27T09:00:00",
            },
            "replay_worker_history": [],
            "replay_worker_alert": {
                "level": "healthy",
                "headline": "Replay worker healthy",
                "detail": "Last heartbeat is idle with 0 queued replay runs remaining.",
                "latest_failure_status": None,
                "latest_failure_at": None,
                "latest_failure_message": None,
            },
            "replay_worker_alert_policy": {
                "workspace_id": None,
                "warning_consecutive_failures": 1,
                "critical_consecutive_failures": 3,
                "default_warning_consecutive_failures": 1,
                "default_critical_consecutive_failures": 3,
                "source": "default",
                "updated_at": None,
            },
        },
        run_remote_provider_smoke=lambda command, **kwargs: {
            "providers": list(command.providers) or ["deployment_lookup"],
            "summary": {
                "success_count": 0,
                "skipped_count": len(list(command.providers) or ["deployment_lookup"]),
                "failed_count": 0,
            },
            "results": [
                {
                    "provider": provider,
                    "status": "skipped",
                    "reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                    "capability": {
                        "requested_mode": "auto",
                        "effective_mode": "local",
                        "backend_id": "heuristic-provider",
                        "fallback_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                        "details": {"fallback_enabled": True},
                    },
                    "request": {"service_id": command.service_id},
                    "response": None,
                    "provenance": None,
                }
                for provider in (list(command.providers) or ["deployment_lookup"])
            ],
            "exit_code": 0,
            "diagnostic_run_id": "runtime-smoke-stub-1",
            "created_at": "2026-03-27T09:00:06",
        },
        list_remote_provider_smoke_runs=lambda limit=10, actor_user_id=None, request_id=None, provider=None: [
            {
                "diagnostic_run_id": "runtime-smoke-stub-1",
                "actor_type": "user",
                "actor_user_id": "user-admin-1",
                "actor_role": "product_admin",
                "session_id": "session-admin-1",
                "request_id": "req-runtime-smoke-stub-1",
                "request_payload": {
                    "providers": ["deployment_lookup"],
                    "include_write": False,
                    "allow_write": False,
                    "require_configured": False,
                },
                "response": {
                    "providers": ["deployment_lookup"],
                    "summary": {
                        "success_count": 0,
                        "skipped_count": 1,
                        "failed_count": 0,
                    },
                    "results": [
                        {
                            "provider": "deployment_lookup",
                            "status": "skipped",
                            "reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                            "capability": {
                                "requested_mode": "auto",
                                "effective_mode": "local",
                                "backend_id": "heuristic-provider",
                                "fallback_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                                "details": {"fallback_enabled": True},
                            },
                            "request": {"service_id": "checkout-api"},
                            "response": None,
                            "provenance": None,
                        }
                    ],
                    "exit_code": 0,
                    "diagnostic_run_id": "runtime-smoke-stub-1",
                    "created_at": "2026-03-27T09:00:06",
                },
                "created_at": "2026-03-27T09:00:06",
            }
        ][:limit]
        if (
            actor_user_id in {None, "", "user-admin-1"}
            and request_id in {None, "", "req-runtime-smoke-stub-1"}
            and provider in {None, "", "deployment_lookup"}
        )
        else [],
        summarize_remote_provider_smoke_runs=lambda limit=50, actor_user_id=None, request_id=None, provider=None: {
            "scanned_run_count": 1,
            "provider_count": 1,
            "providers": [
                {
                    "provider": "deployment_lookup",
                    "run_count": 1,
                    "success_count": 0,
                    "skipped_count": 1,
                    "failed_count": 0,
                    "last_status": "skipped",
                    "last_reason": "REMOTE_PROVIDER_NOT_ACTIVE",
                    "last_seen_at": "2026-03-27T09:00:06",
                    "last_success_at": None,
                    "last_failure_at": None,
                    "last_skipped_at": "2026-03-27T09:00:06",
                    "last_diagnostic_run_id": "runtime-smoke-stub-1",
                    "latest_effective_mode": "local",
                    "latest_backend_id": "heuristic-provider",
                    "latest_strict_remote_required": False,
                }
            ],
        }
        if (
            limit >= 1
            and actor_user_id in {None, "", "user-admin-1"}
            and request_id in {None, "", "req-runtime-smoke-stub-1"}
            and provider in {None, "", "deployment_lookup"}
        )
        else {"scanned_run_count": 0, "provider_count": 0, "providers": []},
        get_replay_quality_summary=lambda workspace_id, incident_id=None: {
            "workspace_id": workspace_id,
            "incident_id": incident_id,
            "incident_count": 1,
            "replay_case_count": 1,
            "replay_case_expected_output_count": 1,
            "replay_case_expected_output_coverage_rate": 1.0,
            "baseline_count": 1,
            "baseline_incident_coverage_count": 1,
            "baseline_coverage_rate": 1.0,
            "evaluation_count": 1,
            "matched_evaluation_count": 1,
            "mismatched_evaluation_count": 0,
            "replay_pass_rate": 1.0,
            "avg_replay_score": 1.0,
            "semantic_evaluation_count": 1,
            "avg_semantic_match_rate": 1.0,
            "avg_top_hypothesis_hit_rate": 1.0,
            "avg_recommendation_match_rate": 1.0,
            "avg_comms_match_rate": 1.0,
            "latest_report_id": "report-stub-1",
            "latest_report_created_at": "2026-03-27T09:00:10",
        },
        get_replay_worker_alert_policy=lambda workspace_id: {
            "workspace_id": workspace_id,
            "warning_consecutive_failures": 1,
            "critical_consecutive_failures": 3,
            "default_warning_consecutive_failures": 1,
            "default_critical_consecutive_failures": 3,
            "source": "default",
            "updated_at": None,
        },
        list_replay_worker_monitor_presets=lambda workspace_id, shift_label=None: [
            {
                "workspace_id": workspace_id,
                "preset_name": "night-shift",
                "history_limit": 10,
                "actor_user_id": "user-admin-1",
                "request_id": "req-monitor-1",
                "policy_audit_limit": 5,
                "policy_audit_copy_format": "markdown",
                "policy_audit_include_summary": True,
                "is_default": True,
                "default_source": "shift_default" if shift_label else "workspace_default",
                "updated_at": "2026-03-27T09:00:02",
            }
        ],
        get_replay_worker_monitor_shift_schedule=lambda workspace_id: {
            "workspace_id": workspace_id,
            "timezone": "UTC",
            "windows": [
                {"shift_label": "day", "start_time": "08:00", "end_time": "20:00"},
                {"shift_label": "night", "start_time": "20:00", "end_time": "08:00"},
            ],
            "date_overrides": [
                {
                    "date": "2026-03-27",
                    "note": "Holiday",
                    "windows": [
                        {"shift_label": "holiday", "start_time": "10:00", "end_time": "14:00"},
                    ],
                }
            ],
            "date_range_overrides": [
                {
                    "start_date": "2026-03-28",
                    "end_date": "2026-03-30",
                    "note": "Migration week",
                    "windows": [
                        {"shift_label": "migration", "start_time": "09:00", "end_time": "18:00"},
                    ],
                }
            ],
            "updated_at": "2026-03-27T09:00:01",
        },
        update_replay_worker_monitor_shift_schedule=lambda workspace_id, command, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "timezone": command.timezone,
            "windows": [window.model_dump(mode="json") for window in command.windows],
            "date_overrides": [override.model_dump(mode="json") for override in command.date_overrides],
            "date_range_overrides": [override.model_dump(mode="json") for override in command.date_range_overrides],
            "updated_at": "2026-03-27T09:00:05",
        },
        clear_replay_worker_monitor_shift_schedule=lambda workspace_id, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "cleared": True,
        },
        resolve_replay_worker_monitor_shift_label=lambda workspace_id, evaluated_at=None: {
            "workspace_id": workspace_id,
            "timezone": "UTC",
            "evaluated_at": "2026-03-27T21:00:00Z",
            "shift_label": "holiday",
            "source": "date_override",
            "matched_window": {
                "shift_label": "holiday",
                "start_time": "10:00",
                "end_time": "14:00",
            },
            "override_date": "2026-03-27",
            "override_range_start_date": None,
            "override_range_end_date": None,
            "override_note": "Holiday",
            "updated_at": "2026-03-27T09:00:01",
        },
        get_replay_worker_monitor_default_preset=lambda workspace_id, shift_label=None: {
            "workspace_id": workspace_id,
            "preset_name": "night-shift",
            "shift_label": shift_label,
            "source": "shift_default" if shift_label else "workspace_default",
            "updated_at": "2026-03-27T09:00:02",
            "cleared": False,
        },
        upsert_replay_worker_monitor_preset=lambda workspace_id, preset_name, command, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "preset_name": preset_name,
            "history_limit": command.history_limit,
            "actor_user_id": command.actor_user_id,
            "request_id": command.request_id,
            "policy_audit_limit": command.policy_audit_limit,
            "policy_audit_copy_format": command.policy_audit_copy_format,
            "policy_audit_include_summary": command.policy_audit_include_summary,
            "is_default": False,
            "default_source": "none",
            "updated_at": "2026-03-27T09:00:03",
        },
        set_replay_worker_monitor_default_preset=lambda workspace_id, preset_name, shift_label=None, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "preset_name": preset_name,
            "shift_label": shift_label,
            "source": "shift_default" if shift_label else "workspace_default",
            "updated_at": "2026-03-27T09:00:04",
            "cleared": False,
        },
        clear_replay_worker_monitor_default_preset=lambda workspace_id, shift_label=None, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "preset_name": "night-shift",
            "shift_label": shift_label,
            "source": "shift_default" if shift_label else "workspace_default",
            "updated_at": "2026-03-27T09:00:04",
            "cleared": True,
        },
        delete_replay_worker_monitor_preset=lambda workspace_id, preset_name, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "preset_name": preset_name,
            "deleted": True,
        },
        update_replay_worker_alert_policy=lambda workspace_id, command, auth_context=None, request_id=None: {
            "workspace_id": workspace_id,
            "warning_consecutive_failures": command.warning_consecutive_failures,
            "critical_consecutive_failures": command.critical_consecutive_failures,
            "default_warning_consecutive_failures": 1,
            "default_critical_consecutive_failures": 3,
            "source": (
                "default"
                if (
                    command.warning_consecutive_failures == 1
                    and command.critical_consecutive_failures == 3
                )
                else "workspace_override"
            ),
            "updated_at": None,
        },
        get_replay_worker_status=lambda workspace_id=None, history_limit=10: {
            "workspace_id": workspace_id or "ops-ws-1",
            "current": {
                "workspace_id": workspace_id or "ops-ws-1",
                "status": "idle",
                "iteration": 2,
                "attempted_count": 0,
                "dispatched_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "idle_polls": 1,
                "consecutive_failures": 0,
                "remaining_queued_count": 0,
                "error_message": None,
                "last_seen_at": "2026-03-27T09:00:00",
            },
            "history": [
                {
                    "workspace_id": workspace_id or "ops-ws-1",
                    "status": "idle",
                    "iteration": 2,
                    "attempted_count": 0,
                    "dispatched_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "idle_polls": 1,
                    "consecutive_failures": 0,
                    "remaining_queued_count": 0,
                    "error_message": None,
                    "emitted_at": "2026-03-27T09:00:00",
                },
                {
                    "workspace_id": workspace_id or "ops-ws-1",
                    "status": "active",
                    "iteration": 1,
                    "attempted_count": 1,
                    "dispatched_count": 1,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "idle_polls": 0,
                    "consecutive_failures": 0,
                    "remaining_queued_count": 0,
                    "error_message": None,
                    "emitted_at": "2026-03-27T08:59:59",
                },
            ][:history_limit],
            "policy": {
                "workspace_id": workspace_id or "ops-ws-1",
                "warning_consecutive_failures": 1,
                "critical_consecutive_failures": 3,
                "default_warning_consecutive_failures": 1,
                "default_critical_consecutive_failures": 3,
                "source": "default",
                "updated_at": None,
            },
        },
        list_replay_admin_audit_logs=lambda workspace_id, action_type=None, actor_user_id=None, request_id=None: [
            {
                "id": "replay-audit-1",
                "workspace_id": workspace_id,
                "action_type": action_type or "replay.update_worker_alert_policy",
                "actor_type": "user",
                "actor_user_id": actor_user_id or "user-admin-1",
                "actor_role": "product_admin",
                "session_id": "session-admin-1",
                "request_id": request_id or "req-replay-policy-1",
                "idempotency_key": None,
                "subject_type": "replay_worker_alert_policy",
                "subject_id": workspace_id,
                "request_payload": {"warning_consecutive_failures": 2, "critical_consecutive_failures": 4},
                "result_payload": {"source": "workspace_override"},
                "created_at": "2026-03-27T09:00:01",
            }
        ],
        list_workflows=lambda: [],
        get_workflow_state=lambda workflow_run_id: {"workflow_run_id": workflow_run_id},
        list_incidents=lambda workspace_id, status=None, severity=None, service_id=None: [{"id": "incident-1"}],
        add_fact=lambda incident_id, command, idempotency_key=None, **kwargs: {
            "fact_id": "fact-1",
            "status": "confirmed",
            "current_fact_set_version": 2,
        },
        start_replay_run=lambda command, idempotency_key=None, **kwargs: SimpleNamespace(
            workflow_run_id=None,
            model_dump=lambda **kwargs: {
                "workflow_name": "opsgraph_incident_response",
                "id": "replay-queued-1",
                "incident_id": "incident-1",
                "status": "queued",
                "model_bundle_version": "bundle-v1",
                "replay_case_id": None,
                "workflow_run_id": None,
                "current_state": None,
                "error_message": None,
                "created_at": "2026-03-17T10:00:00",
            },
        ),
        process_queued_replays=lambda workspace_id, limit=20, **kwargs: {
            "workspace_id": workspace_id,
            "queued_count": 1,
            "processed_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
            "remaining_queued_count": 0,
            "items": [
                {
                    "id": "replay-queued-1",
                    "incident_id": "incident-1",
                    "status": "completed",
                    "model_bundle_version": "bundle-v1",
                    "replay_case_id": None,
                    "workflow_run_id": "replay-queued-1-replay",
                    "current_state": "resolve",
                    "error_message": None,
                    "created_at": "2026-03-17T10:00:00",
                }
            ],
        },
        runtime_stores=None,
    )


if __name__ == "__main__":
    unittest.main()
