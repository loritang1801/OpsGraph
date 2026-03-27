from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import uuid4

from .errors import SharedAuthorizationError


DEFAULT_ACCESS_TTL_SECONDS = 60 * 60
DEFAULT_PBKDF2_ITERATIONS = 200_000


def _auth_error(
    *,
    error_type: type[Exception],
    code: str,
    message: str,
    status_code: int,
) -> Exception:
    if issubclass(error_type, SharedAuthorizationError):
        return error_type(code=code, message=message, status_code=status_code)
    return error_type(message)


def env_secret(name: str, *, default: str, env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else {}
    value = source.get(name)
    if value is None:
        import os

        value = os.environ.get(name)
    normalized = (value or "").strip()
    return normalized or default


def extract_bearer_token(
    authorization: str | None,
    *,
    error_type: type[Exception] = SharedAuthorizationError,
) -> str:
    token = (authorization or "").strip()
    if not token:
        raise _auth_error(
            error_type=error_type,
            code="AUTH_REQUIRED",
            message="Authorization header is required.",
            status_code=401,
        )
    if not token.lower().startswith("bearer "):
        raise _auth_error(
            error_type=error_type,
            code="AUTH_INVALID_CREDENTIALS",
            message="Authorization header must use the Bearer scheme.",
            status_code=401,
        )
    normalized = token[7:].strip()
    if not normalized:
        raise _auth_error(
            error_type=error_type,
            code="AUTH_INVALID_CREDENTIALS",
            message="Malformed access token.",
            status_code=401,
        )
    return normalized


def normalize_role(
    role: str,
    *,
    role_priority: Mapping[str, int],
    role_aliases: Mapping[str, str] | None = None,
    error_type: type[Exception] = SharedAuthorizationError,
) -> str:
    aliases = role_aliases or {}
    normalized = aliases.get(role.strip().lower(), role.strip().lower())
    if normalized not in role_priority:
        raise _auth_error(
            error_type=error_type,
            code="AUTH_CONTEXT_INVALID",
            message=f"Unsupported role '{role}'.",
            status_code=400,
        )
    return normalized


def require_role(
    *,
    required_role: str,
    actual_role: str,
    role_priority: Mapping[str, int],
    role_aliases: Mapping[str, str] | None = None,
    error_type: type[Exception] = SharedAuthorizationError,
) -> str:
    normalized_required_role = normalize_role(
        required_role,
        role_priority=role_priority,
        role_aliases=role_aliases,
        error_type=error_type,
    )
    normalized_actual_role = normalize_role(
        actual_role,
        role_priority=role_priority,
        role_aliases=role_aliases,
        error_type=error_type,
    )
    if role_priority[normalized_actual_role] < role_priority[normalized_required_role]:
        raise _auth_error(
            error_type=error_type,
            code="AUTH_FORBIDDEN",
            message=(
                f"Role '{normalized_actual_role}' does not satisfy required role "
                f"'{normalized_required_role}'."
            ),
            status_code=403,
        )
    return normalized_actual_role


class AccessTokenCodec:
    def __init__(
        self,
        secret: str,
        *,
        ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        error_type: type[Exception] = SharedAuthorizationError,
    ) -> None:
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds
        self.error_type = error_type

    def mint(self, **claims: str) -> tuple[str, datetime]:
        expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        payload = {
            **claims,
            "jti": uuid4().hex,
            "exp": int(expires_at.timestamp()),
        }
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.secret, payload_bytes, hashlib.sha256).digest()
        token = (
            base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )
        return token, expires_at

    def parse(self, token: str) -> dict[str, object]:
        try:
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise _auth_error(
                error_type=self.error_type,
                code="AUTH_INVALID_CREDENTIALS",
                message="Malformed access token.",
                status_code=401,
            ) from exc
        payload_bytes = self._decode_segment(encoded_payload)
        actual_signature = self._decode_segment(encoded_signature)
        expected_signature = hmac.new(self.secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(actual_signature, expected_signature):
            raise _auth_error(
                error_type=self.error_type,
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token signature.",
                status_code=401,
            )
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _auth_error(
                error_type=self.error_type,
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token payload.",
                status_code=401,
            ) from exc
        expires_at = int(payload.get("exp", 0))
        if expires_at <= int(datetime.now(UTC).timestamp()):
            raise _auth_error(
                error_type=self.error_type,
                code="AUTH_ACCESS_EXPIRED",
                message="Access token has expired.",
                status_code=401,
            )
        return payload

    def _decode_segment(self, value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        try:
            return base64.urlsafe_b64decode((value + padding).encode("ascii"))
        except (ValueError, binascii.Error) as exc:
            raise _auth_error(
                error_type=self.error_type,
                code="AUTH_INVALID_CREDENTIALS",
                message="Malformed access token encoding.",
                status_code=401,
            ) from exc


def hash_token_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password_pbkdf2(
    password: str,
    *,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password_pbkdf2(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        return False
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = password_hash.split("$", maxsplit=3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or not raw_iterations.isdigit():
        return False
    salt = bytes.fromhex(salt_hex)
    expected_digest = bytes.fromhex(digest_hex)
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        int(raw_iterations),
    )
    return hmac.compare_digest(computed, expected_digest)
