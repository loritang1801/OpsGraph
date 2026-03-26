from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.auth import HeaderOpsGraphAuthorizer, OpsGraphAuthorizationError
from opsgraph_app.bootstrap import build_app_service
from opsgraph_app.routes import create_fastapi_app

try:
    from fastapi.testclient import TestClient
except Exception:  # noqa: BLE001
    TestClient = None


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
        self.client = TestClient(create_fastapi_app(_stub_service()))

    def tearDown(self) -> None:
        self.client.close()

    def test_health_route_remains_public(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "ok")

    def test_viewer_route_requires_authorization_header(self) -> None:
        response = self.client.get(
            "/api/v1/opsgraph/incidents",
            params={"workspace_id": "ops-ws-1"},
            headers={"X-Organization-Id": "org-1"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_viewer_route_requires_organization_context(self) -> None:
        response = self.client.get(
            "/api/v1/opsgraph/incidents",
            params={"workspace_id": "ops-ws-1"},
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "TENANT_CONTEXT_REQUIRED")

    def test_viewer_route_allows_viewer_role(self) -> None:
        response = self.client.get(
            "/api/v1/opsgraph/incidents",
            params={"workspace_id": "ops-ws-1"},
            headers=_auth_headers(role="viewer"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["id"], "incident-1")

    def test_operator_route_rejects_viewer_role(self) -> None:
        response = self.client.post(
            "/api/v1/opsgraph/incidents/incident-1/facts",
            headers={
                **_auth_headers(role="viewer"),
                "Idempotency-Key": "fact-create-1",
            },
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_operator_route_allows_org_admin_alias(self) -> None:
        response = self.client.post(
            "/api/v1/opsgraph/incidents/incident-1/facts",
            headers={
                **_auth_headers(role="org_admin"),
                "Idempotency-Key": "fact-create-2",
            },
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["fact_id"], "fact-1")

    def test_replay_trigger_requires_product_admin_access(self) -> None:
        response = self.client.post(
            "/api/v1/opsgraph/replays/run",
            headers={
                **_auth_headers(role="operator"),
                "Idempotency-Key": "replay-run-1",
            },
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "bundle-v1",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_replay_trigger_allows_product_admin_alias(self) -> None:
        response = self.client.post(
            "/api/v1/opsgraph/replays/run",
            headers={
                **_auth_headers(role="org_admin"),
                "Idempotency-Key": "replay-run-2",
            },
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "bundle-v1",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["meta"]["workflow_run_id"], "wf-replay-1")


@unittest.skipIf(TestClient is None, "fastapi test client unavailable")
class OpsGraphAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = build_app_service()
        self.addCleanup(self.service.close)
        self.client = TestClient(create_fastapi_app(self.service))

    def tearDown(self) -> None:
        self.client.close()

    def test_session_routes_issue_cookie_and_authorize_me(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "operator@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertIn("refresh_token", login_response.cookies)
        access_token = login_response.json()["data"]["access_token"]

        me_response = self.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["data"]["active_organization"]["slug"], "acme")

    def test_header_auth_still_works_when_auth_service_is_enabled(self) -> None:
        response = self.client.get(
            "/api/v1/opsgraph/incidents",
            params={"workspace_id": "ops-ws-1"},
            headers=_auth_headers(role="viewer"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["id"], "incident-1")

    def test_refresh_and_revoke_current_session(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )
        access_token = login_response.json()["data"]["access_token"]

        refresh_response = self.client.post("/api/v1/auth/session/refresh")
        self.assertEqual(refresh_response.status_code, 200)
        refreshed_access_token = refresh_response.json()["data"]["access_token"]
        self.assertNotEqual(refreshed_access_token, access_token)

        revoke_response = self.client.delete(
            "/api/v1/auth/session/current",
            headers={"Authorization": f"Bearer {refreshed_access_token}"},
        )
        self.assertEqual(revoke_response.status_code, 204)

        blocked_response = self.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {refreshed_access_token}"},
        )
        self.assertEqual(blocked_response.status_code, 401)
        self.assertEqual(blocked_response.json()["error"]["code"], "AUTH_SESSION_REVOKED")

    def test_session_admin_can_trigger_replay_route_and_read_audit_logs(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )
        access_token = login_response.json()["data"]["access_token"]

        replay_response = self.client.post(
            "/api/v1/opsgraph/replays/run",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Idempotency-Key": "route-replay-session-1",
            },
            json={
                "incident_id": "incident-1",
                "model_bundle_version": "route-bundle-v1",
            },
        )
        audit_response = self.client.get(
            "/api/v1/opsgraph/incidents/incident-1/audit-logs",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"action_type": "replay.start_run"},
        )

        self.assertEqual(replay_response.status_code, 202)
        self.assertEqual(audit_response.status_code, 200)
        self.assertTrue(
            any(item["action_type"] == "replay.start_run" for item in audit_response.json()["data"])
        )

    def test_membership_admin_routes_require_product_admin_access(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "operator@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )
        access_token = login_response.json()["data"]["access_token"]

        response = self.client.get(
            "/api/v1/auth/memberships",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_membership_admin_routes_provision_update_and_suspend_member(self) -> None:
        admin_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )
        admin_access_token = admin_login.json()["data"]["access_token"]

        list_response = self.client.get(
            "/api/v1/auth/memberships",
            headers={"Authorization": f"Bearer {admin_access_token}"},
        )
        create_response = self.client.post(
            "/api/v1/auth/memberships",
            headers={"Authorization": f"Bearer {admin_access_token}"},
            json={
                "email": "member-route@example.com",
                "display_name": "Member Route",
                "role": "viewer",
                "password": "opsgraph-route-member",
            },
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 200)
        membership_id = create_response.json()["data"]["id"]
        self.assertEqual(create_response.json()["data"]["role"], "viewer")

        member_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "member-route@example.com",
                "password": "opsgraph-route-member",
                "organization_slug": "acme",
            },
        )
        self.assertEqual(member_login.status_code, 200)
        member_access_token = member_login.json()["data"]["access_token"]

        promote_response = self.client.patch(
            f"/api/v1/auth/memberships/{membership_id}",
            headers={"Authorization": f"Bearer {admin_access_token}"},
            json={"role": "operator"},
        )
        revoked_member_response = self.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {member_access_token}"},
        )

        self.assertEqual(promote_response.status_code, 200)
        self.assertEqual(promote_response.json()["data"]["role"], "operator")
        self.assertEqual(revoked_member_response.status_code, 401)
        self.assertEqual(revoked_member_response.json()["error"]["code"], "AUTH_SESSION_REVOKED")

        promoted_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "member-route@example.com",
                "password": "opsgraph-route-member",
                "organization_slug": "acme",
            },
        )
        self.assertEqual(promoted_login.status_code, 200)
        promoted_access_token = promoted_login.json()["data"]["access_token"]

        operator_action = self.client.post(
            "/api/v1/opsgraph/incidents/incident-1/facts",
            headers={
                "Authorization": f"Bearer {promoted_access_token}",
                "Idempotency-Key": "route-member-fact-1",
            },
            json={
                "fact_type": "impact",
                "statement": "Checkout degraded.",
                "source_refs": [],
                "expected_fact_set_version": 1,
            },
        )
        suspend_response = self.client.patch(
            f"/api/v1/auth/memberships/{membership_id}",
            headers={"Authorization": f"Bearer {admin_access_token}"},
            json={"status": "suspended"},
        )
        suspended_member_response = self.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {promoted_access_token}"},
        )
        blocked_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "member-route@example.com",
                "password": "opsgraph-route-member",
                "organization_slug": "acme",
            },
        )

        self.assertEqual(operator_action.status_code, 200)
        self.assertEqual(suspend_response.status_code, 200)
        self.assertEqual(suspend_response.json()["data"]["status"], "suspended")
        self.assertEqual(suspended_member_response.status_code, 401)
        self.assertEqual(suspended_member_response.json()["error"]["code"], "AUTH_SESSION_REVOKED")
        self.assertEqual(blocked_login.status_code, 401)
        self.assertEqual(blocked_login.json()["error"]["code"], "AUTH_INVALID_CREDENTIALS")

    def test_membership_admin_route_prevents_self_lockout(self) -> None:
        admin_login = self.client.post(
            "/api/v1/auth/session",
            json={
                "email": "admin@example.com",
                "password": "opsgraph-demo",
                "organization_slug": "acme",
            },
        )
        admin_access_token = admin_login.json()["data"]["access_token"]
        memberships_response = self.client.get(
            "/api/v1/auth/memberships",
            headers={"Authorization": f"Bearer {admin_access_token}"},
        )
        admin_membership = next(
            item for item in memberships_response.json()["data"] if item["user"]["email"] == "admin@example.com"
        )

        response = self.client.patch(
            f"/api/v1/auth/memberships/{admin_membership['id']}",
            headers={"Authorization": f"Bearer {admin_access_token}"},
            json={"role": "viewer"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "AUTH_SELF_LOCKOUT_FORBIDDEN")


def _auth_headers(*, role: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-Organization-Id": "org-1",
        "X-User-Id": "user-1",
        "X-User-Role": role,
    }


def _stub_service():
    return SimpleNamespace(
        auth_service=None,
        list_workflows=lambda: [],
        get_workflow_state=lambda workflow_run_id: {"workflow_run_id": workflow_run_id},
        list_incidents=lambda workspace_id, status=None, severity=None, service_id=None: [{"id": "incident-1"}],
        add_fact=lambda incident_id, command, idempotency_key=None, **kwargs: {
            "fact_id": "fact-1",
            "status": "confirmed",
            "current_fact_set_version": 2,
        },
        start_replay_run=lambda command, idempotency_key=None, **kwargs: SimpleNamespace(
            workflow_run_id="wf-replay-1",
            model_dump=lambda **kwargs: {
                "workflow_name": "opsgraph_incident_response",
                "workflow_run_id": "wf-replay-1",
                "workflow_type": "opsgraph_incident",
                "current_state": "resolve",
                "checkpoint_seq": 4,
                "emitted_events": [],
            },
        ),
        runtime_stores=None,
    )


if __name__ == "__main__":
    unittest.main()
