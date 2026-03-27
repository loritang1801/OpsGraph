from __future__ import annotations

import unittest
from typing import Any, Callable

_MISSING = object()


def fastapi_test_client_class():
    try:
        from fastapi.testclient import TestClient
    except Exception:  # noqa: BLE001
        return None
    return TestClient


def create_managed_test_client(test_case: unittest.TestCase, app: Any):
    client_class = fastapi_test_client_class()
    if client_class is None:
        raise RuntimeError("fastapi test client unavailable")
    client = client_class(app)
    test_case.addCleanup(client.close)
    return client


def assert_managed_app_service(
    test_case: unittest.TestCase,
    app: Any,
    *,
    state_attr: str,
):
    state = getattr(app, "state", None)
    service = getattr(state, state_attr, None)
    test_case.assertIsNotNone(service)
    if hasattr(service, "close"):
        test_case.addCleanup(service.close)
    test_case.assertIs(getattr(app.state, state_attr), service)
    return service


def assert_json_error_response(
    test_case: unittest.TestCase,
    response: Any,
    *,
    status_code: int,
    error_code: str,
):
    test_case.assertEqual(response.status_code, status_code)
    payload = response.json()
    test_case.assertEqual(payload["error"]["code"], error_code)
    return payload


def assert_domain_error_mapping(
    test_case: unittest.TestCase,
    result: tuple[int, Any],
    *,
    status_code: int,
    error_code: str,
):
    actual_status_code, payload = result
    test_case.assertEqual(actual_status_code, status_code)
    test_case.assertEqual(payload["error"]["code"], error_code)
    return payload


def assert_json_data_response(
    test_case: unittest.TestCase,
    response: Any,
    *,
    status_code: int = 200,
):
    test_case.assertEqual(response.status_code, status_code)
    payload = response.json()
    return payload["data"]


def assert_collection_size(
    test_case: unittest.TestCase,
    items: Any,
    *,
    size: int | None = None,
    min_size: int | None = None,
):
    actual_size = len(items)
    if size is not None:
        test_case.assertEqual(actual_size, size)
    if min_size is not None:
        test_case.assertGreaterEqual(actual_size, min_size)
    return actual_size


def _lookup_field_value(payload: Any, field_path: str) -> Any:
    current = payload
    for segment in field_path.split("."):
        if isinstance(current, dict):
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            current = current[int(segment)]
        else:
            current = getattr(current, segment)
    return current


def _normalize_json_payload(payload_or_response: Any) -> Any:
    json_fn = getattr(payload_or_response, "json", None)
    if callable(json_fn):
        return json_fn()
    return payload_or_response


def assert_fields(
    test_case: unittest.TestCase,
    payload: Any,
    *,
    expected_fields: dict[str, Any],
):
    for field_path, expected_value in expected_fields.items():
        actual_value = _lookup_field_value(payload, field_path)
        test_case.assertEqual(actual_value, expected_value)
    return payload


def assert_success_envelope(
    test_case: unittest.TestCase,
    payload_or_response: Any,
    *,
    data_expected_fields: dict[str, Any] | None = None,
    meta_expected_fields: dict[str, Any] | None = None,
):
    payload = _normalize_json_payload(payload_or_response)
    if data_expected_fields:
        assert_fields(
            test_case,
            payload,
            expected_fields={f"data.{path}": value for path, value in data_expected_fields.items()},
        )
    if meta_expected_fields:
        assert_fields(
            test_case,
            payload,
            expected_fields={f"meta.{path}": value for path, value in meta_expected_fields.items()},
    )
    return payload


def assert_sse_message_contract(
    test_case: unittest.TestCase,
    message: str,
    *,
    event_id: str,
    event_name: str,
    expected_substrings: list[str] | tuple[str, ...] = (),
    resolved_topic: str | None = None,
    expected_topic: str | None = None,
):
    test_case.assertIn(f"id: {event_id}", message)
    test_case.assertIn(f"event: {event_name}", message)
    for expected_substring in expected_substrings:
        test_case.assertIn(expected_substring, message)
    if expected_topic is not None:
        test_case.assertEqual(resolved_topic, expected_topic)
    return message


def assert_paginated_window(
    test_case: unittest.TestCase,
    result: tuple[Any, Any, Any],
    *,
    expected_items: Any,
    has_more: bool,
    next_cursor_present: bool | None = None,
    next_cursor_value: Any = _MISSING,
):
    page_items, next_cursor, actual_has_more = result
    test_case.assertEqual(page_items, expected_items)
    test_case.assertEqual(actual_has_more, has_more)
    if next_cursor_present is True:
        test_case.assertIsNotNone(next_cursor)
    elif next_cursor_present is False:
        test_case.assertIsNone(next_cursor)
    if next_cursor_value is not _MISSING:
        test_case.assertEqual(next_cursor, next_cursor_value)
    return next_cursor


def assert_event_topic_routing(
    test_case: unittest.TestCase,
    context: Any,
    topics: Any,
    *,
    expected_topics: list[str] | tuple[str, ...],
    matcher: Callable[[Any, str], bool],
    matching_topic: str,
    rejected_topic: str,
):
    for expected_topic in expected_topics:
        test_case.assertIn(expected_topic, topics)
    test_case.assertTrue(matcher(context, matching_topic))
    test_case.assertFalse(matcher(context, rejected_topic))
    return topics


def assert_resume_after_id_contract(
    test_case: unittest.TestCase,
    normalizer: Callable[[Any, str], Any],
    pending: Any,
    *,
    missing_id: str,
    existing_id: str,
):
    test_case.assertIsNone(normalizer(pending, missing_id))
    test_case.assertEqual(normalizer(pending, existing_id), existing_id)


def assert_collection_contains(
    test_case: unittest.TestCase,
    items: Any,
    *,
    expected_fields: dict[str, Any],
):
    for item in items:
        if all(_lookup_field_value(item, path) == value for path, value in expected_fields.items()):
            return item
    test_case.fail(f"collection missing item with fields {expected_fields!r}")


def request_and_assert_json_error(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    status_code: int,
    error_code: str,
    **kwargs: Any,
):
    response = client.request(method, path, **kwargs)
    assert_json_error_response(
        test_case,
        response,
        status_code=status_code,
        error_code=error_code,
    )
    return response


def request_and_get_data(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    status_code: int = 200,
    **kwargs: Any,
):
    response = client.request(method, path, **kwargs)
    data = assert_json_data_response(
        test_case,
        response,
        status_code=status_code,
    )
    return response, data


def assert_health_response(
    test_case: unittest.TestCase,
    response: Any,
    *,
    product: str | None = None,
):
    test_case.assertEqual(response.status_code, 200)
    payload = response.json()
    test_case.assertEqual(payload["data"]["status"], "ok")
    if product is not None:
        test_case.assertEqual(payload["data"]["product"], product)
    return payload


def login_via_session_route(
    test_case: unittest.TestCase,
    client: Any,
    *,
    email: str,
    password: str,
    organization_slug: str = "acme",
    path: str = "/api/v1/auth/session",
):
    response = client.post(
        path,
        json={
            "email": email,
            "password": password,
            "organization_slug": organization_slug,
        },
    )
    test_case.assertEqual(response.status_code, 200)
    test_case.assertIn("refresh_token", response.cookies)
    payload = response.json()
    return response, payload["data"]["access_token"]


def refresh_session_access_token(
    test_case: unittest.TestCase,
    client: Any,
    *,
    path: str = "/api/v1/auth/session/refresh",
):
    response = client.post(path)
    test_case.assertEqual(response.status_code, 200)
    payload = response.json()
    return response, payload["data"]["access_token"]


def revoke_current_session(
    test_case: unittest.TestCase,
    client: Any,
    *,
    access_token: str,
    path: str = "/api/v1/auth/session/current",
):
    response = request_with_bearer(
        client,
        "DELETE",
        path,
        access_token=access_token,
    )
    test_case.assertEqual(response.status_code, 204)
    return response


def bearer_auth_headers(
    access_token: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    merged_headers = {"Authorization": f"Bearer {access_token}"}
    if headers:
        merged_headers.update(headers)
    return merged_headers


def header_auth_headers(
    *,
    role: str,
    organization_id: str = "org-1",
    user_id: str = "user-1",
    access_token: str = "test-token",
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    merged_headers = bearer_auth_headers(access_token, headers=headers)
    merged_headers["X-Organization-Id"] = organization_id
    merged_headers["X-User-Id"] = user_id
    merged_headers["X-User-Role"] = role
    return merged_headers


def request_with_bearer(
    client: Any,
    method: str,
    path: str,
    *,
    access_token: str,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    return client.request(
        method,
        path,
        headers=bearer_auth_headers(access_token, headers=headers),
        **kwargs,
    )


def request_with_header_auth(
    client: Any,
    method: str,
    path: str,
    *,
    role: str,
    organization_id: str = "org-1",
    user_id: str = "user-1",
    access_token: str = "test-token",
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    return client.request(
        method,
        path,
        headers=header_auth_headers(
            role=role,
            organization_id=organization_id,
            user_id=user_id,
            access_token=access_token,
            headers=headers,
        ),
        **kwargs,
    )


def request_with_bearer_and_assert_json_error(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    access_token: str,
    status_code: int,
    error_code: str,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    response = request_with_bearer(
        client,
        method,
        path,
        access_token=access_token,
        headers=headers,
        **kwargs,
    )
    assert_json_error_response(
        test_case,
        response,
        status_code=status_code,
        error_code=error_code,
    )
    return response


def request_with_header_auth_and_assert_json_error(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    role: str,
    status_code: int,
    error_code: str,
    organization_id: str = "org-1",
    user_id: str = "user-1",
    access_token: str = "test-token",
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    response = request_with_header_auth(
        client,
        method,
        path,
        role=role,
        organization_id=organization_id,
        user_id=user_id,
        access_token=access_token,
        headers=headers,
        **kwargs,
    )
    assert_json_error_response(
        test_case,
        response,
        status_code=status_code,
        error_code=error_code,
    )
    return response


def request_with_bearer_and_get_data(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    access_token: str,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    response = request_with_bearer(
        client,
        method,
        path,
        access_token=access_token,
        headers=headers,
        **kwargs,
    )
    data = assert_json_data_response(
        test_case,
        response,
        status_code=status_code,
    )
    return response, data


def request_with_header_auth_and_get_data(
    test_case: unittest.TestCase,
    client: Any,
    method: str,
    path: str,
    *,
    role: str,
    status_code: int = 200,
    organization_id: str = "org-1",
    user_id: str = "user-1",
    access_token: str = "test-token",
    headers: dict[str, str] | None = None,
    **kwargs: Any,
):
    response = request_with_header_auth(
        client,
        method,
        path,
        role=role,
        organization_id=organization_id,
        user_id=user_id,
        access_token=access_token,
        headers=headers,
        **kwargs,
    )
    data = assert_json_data_response(
        test_case,
        response,
        status_code=status_code,
    )
    return response, data


def get_current_user_via_bearer(
    test_case: unittest.TestCase,
    client: Any,
    *,
    access_token: str,
    path: str = "/api/v1/me",
):
    response = request_with_bearer(
        client,
        "GET",
        path,
        access_token=access_token,
    )
    data = assert_json_data_response(test_case, response, status_code=200)
    return response, data
