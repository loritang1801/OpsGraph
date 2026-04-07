from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, DateTime, String, Text, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .shared_runtime import load_shared_agent_platform


ROLE_PRIORITY = {
    "viewer": 1,
    "operator": 2,
    "product_admin": 3,
}

ROLE_ALIASES = {
    "org_admin": "product_admin",
}

DEFAULT_AUTH_SECRET = "opsgraph-dev-secret"
DEFAULT_ACCESS_TTL_SECONDS = 60 * 60
DEFAULT_REFRESH_TTL_DAYS = 30
PBKDF2_ITERATIONS = 120_000


@dataclass(frozen=True, slots=True)
class OpsGraphAccessContext:
    organization_id: str
    user_id: str
    role: str
    session_id: str | None = None


class OpsGraphAuthorizationError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class OpsGraphAuthorizer(Protocol):
    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> OpsGraphAccessContext: ...


class OpsGraphAuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionUser(OpsGraphAuthModel):
    user_id: str = Field(serialization_alias="id")
    email: str
    display_name: str


class SessionOrganization(OpsGraphAuthModel):
    organization_id: str = Field(serialization_alias="id")
    slug: str
    name: str
    status: str


class SessionMembership(OpsGraphAuthModel):
    organization_id: str
    organization_slug: str
    organization_name: str
    role: str


class SessionResponse(OpsGraphAuthModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: SessionUser
    active_organization: SessionOrganization
    memberships: list[SessionMembership] = Field(default_factory=list)


class CurrentUserResponse(OpsGraphAuthModel):
    user: SessionUser
    active_organization: SessionOrganization
    memberships: list[SessionMembership] = Field(default_factory=list)


class ManagedUserSummary(OpsGraphAuthModel):
    user_id: str = Field(serialization_alias="id")
    email: str
    display_name: str
    status: str


class ManagedMembershipSummary(OpsGraphAuthModel):
    membership_id: str = Field(serialization_alias="id")
    organization_id: str
    organization_slug: str
    organization_name: str
    user: ManagedUserSummary
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class SessionCreateCommand(OpsGraphAuthModel):
    email: str
    password: str
    organization_slug: str


class MembershipProvisionCommand(OpsGraphAuthModel):
    email: str
    role: str
    display_name: str | None = None
    password: str | None = None


class MembershipUpdateCommand(OpsGraphAuthModel):
    role: str | None = None
    status: Literal["active", "suspended"] | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthSessionIssue:
    session_id: str
    response: SessionResponse
    refresh_token: str


@dataclass(frozen=True, slots=True)
class OpsGraphBootstrapAdminSeed:
    email: str
    password: str
    display_name: str = "OpsGraph Admin"
    organization_slug: str = "opsgraph"
    organization_name: str = "OpsGraph"


class AuthBase(DeclarativeBase):
    pass


class OrganizationRow(AuthBase):
    __tablename__ = "opsgraph_auth_organization"

    organization_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40))
    settings_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class AppUserRow(AuthBase):
    __tablename__ = "opsgraph_auth_user"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class OrganizationMembershipRow(AuthBase):
    __tablename__ = "opsgraph_auth_membership"

    membership_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class AuthSessionRow(AuthBase):
    __tablename__ = "opsgraph_auth_session"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    organization_id: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(40))
    refresh_token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    replaced_by_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


def _normalize_role(role: str | None) -> str:
    normalized = str(role or "").strip().lower()
    if not normalized:
        return "viewer"
    return ROLE_ALIASES.get(normalized, normalized)


def _require_role(*, required_role: str, actual_role: str) -> str:
    normalized_required_role = _normalize_role(required_role)
    normalized_actual_role = _normalize_role(actual_role)
    if normalized_required_role not in ROLE_PRIORITY:
        raise OpsGraphAuthorizationError(
            code="AUTH_CONFIGURATION_ERROR",
            message=f"Unknown required role: {required_role}",
            status_code=500,
        )
    if normalized_actual_role not in ROLE_PRIORITY:
        raise OpsGraphAuthorizationError(
            code="AUTH_INVALID_ROLE",
            message=f"Unknown user role: {actual_role}",
            status_code=400,
        )
    if ROLE_PRIORITY[normalized_actual_role] < ROLE_PRIORITY[normalized_required_role]:
        raise OpsGraphAuthorizationError(
            code="AUTH_FORBIDDEN",
            message="Insufficient role for this operation.",
            status_code=403,
        )
    return normalized_actual_role


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _default_display_name(email: str) -> str:
    local_part = email.partition("@")[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return local_part.title() or email


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def hash_password_pbkdf2(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{_urlsafe_b64encode(salt)}$"
        f"{_urlsafe_b64encode(digest)}"
    )


def verify_password_pbkdf2(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = _urlsafe_b64decode(raw_salt)
        expected_digest = _urlsafe_b64decode(raw_digest)
    except (ValueError, binascii.Error):
        return False
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


class AccessTokenCodec:
    def __init__(self, secret: str, *, ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS) -> None:
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(
        self,
        *,
        session_id: str,
        user_id: str,
        organization_id: str,
        role: str,
    ) -> tuple[str, datetime]:
        expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        payload = {
            "typ": "access",
            "sid": session_id,
            "sub": user_id,
            "org": organization_id,
            "role": role,
            "exp": int(expires_at.timestamp()),
        }
        encoded_payload = _urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(
            self.secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{_urlsafe_b64encode(signature)}", expires_at

    def looks_issued_token(self, token: str) -> bool:
        encoded_payload, separator, encoded_signature = token.partition(".")
        if separator != "." or not encoded_payload or not encoded_signature:
            return False
        try:
            payload = json.loads(_urlsafe_b64decode(encoded_payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("typ") == "access"
            and {"sid", "sub", "org", "role", "exp"}.issubset(payload)
        )

    def parse(self, token: str) -> dict[str, object]:
        encoded_payload, separator, encoded_signature = token.partition(".")
        if separator != "." or not encoded_payload or not encoded_signature:
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            )
        try:
            signature = _urlsafe_b64decode(encoded_signature)
        except (ValueError, binascii.Error) as exc:
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            ) from exc
        expected_signature = hmac.new(
            self.secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            )
        try:
            payload = json.loads(_urlsafe_b64decode(encoded_payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            ) from exc
        if not isinstance(payload, dict) or payload.get("typ") != "access":
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            )
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or expires_at < int(datetime.now(UTC).timestamp()):
            raise OpsGraphAuthorizationError(
                code="AUTH_SESSION_EXPIRED",
                message="Session access token has expired.",
                status_code=401,
            )
        return payload


class HeaderOpsGraphAuthorizer:
    def __init__(self, *, default_role: str = "viewer") -> None:
        self.default_role = default_role

    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> OpsGraphAccessContext:
        if authorization is None or not authorization.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_REQUIRED",
                message="Authorization header is required.",
                status_code=401,
            )
        if organization_id is None or not organization_id.strip():
            raise OpsGraphAuthorizationError(
                code="TENANT_CONTEXT_REQUIRED",
                message="X-Organization-Id header is required.",
                status_code=400,
            )
        normalized_actual_role = _require_role(
            required_role=required_role,
            actual_role=user_role or self.default_role,
        )
        return OpsGraphAccessContext(
            organization_id=organization_id,
            user_id=(user_id or "demo-user"),
            role=normalized_actual_role,
        )


class SessionTokenOpsGraphAuthorizer:
    def __init__(self, auth_service: "SqlAlchemyOpsGraphAuthService") -> None:
        self.auth_service = auth_service

    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> OpsGraphAccessContext:
        del user_id, user_role
        if authorization is None or not authorization.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_REQUIRED",
                message="Authorization header is required.",
                status_code=401,
            )
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid access token.",
                status_code=401,
            )
        return self.auth_service.authorize_access_token(
            token.strip(),
            required_role=required_role,
            organization_id=organization_id,
        )


class HybridOpsGraphAuthorizer:
    def __init__(
        self,
        auth_service: "SqlAlchemyOpsGraphAuthService",
        *,
        header_authorizer: HeaderOpsGraphAuthorizer | None = None,
        allow_header_fallback: bool = True,
    ) -> None:
        self.auth_service = auth_service
        self.header_authorizer = header_authorizer or HeaderOpsGraphAuthorizer()
        self.session_authorizer = SessionTokenOpsGraphAuthorizer(auth_service)
        self.allow_header_fallback = allow_header_fallback

    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> OpsGraphAccessContext:
        if authorization is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and self.auth_service.access_token_codec.looks_issued_token(token.strip()):
                return self.session_authorizer.authorize(
                    required_role=required_role,
                    authorization=authorization,
                    organization_id=organization_id,
                    user_id=user_id,
                    user_role=user_role,
                )
        if not self.allow_header_fallback:
            if authorization is None or not authorization.strip():
                raise OpsGraphAuthorizationError(
                    code="AUTH_REQUIRED",
                    message="Authorization header is required.",
                    status_code=401,
                )
            raise OpsGraphAuthorizationError(
                code="AUTH_SESSION_REQUIRED",
                message="Session-backed authentication is required.",
                status_code=401,
            )
        return self.header_authorizer.authorize(
            required_role=required_role,
            authorization=authorization,
            organization_id=organization_id,
            user_id=user_id,
            user_role=user_role,
        )


def create_auth_tables(engine: Engine) -> None:
    AuthBase.metadata.create_all(engine)


class SqlAlchemyOpsGraphAuthService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: Engine,
        *,
        auth_secret: str | None = None,
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        refresh_ttl_days: int = DEFAULT_REFRESH_TTL_DAYS,
        allow_header_fallback: bool = True,
        seed_demo_users: bool = True,
        bootstrap_admin: OpsGraphBootstrapAdminSeed | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_days = refresh_ttl_days
        self.allow_header_fallback = allow_header_fallback
        self.seed_demo_users = seed_demo_users
        self.bootstrap_admin = bootstrap_admin
        create_auth_tables(engine)
        self.access_token_codec = AccessTokenCodec(
            auth_secret or os.getenv("OPSGRAPH_AUTH_SECRET") or DEFAULT_AUTH_SECRET,
            ttl_seconds=access_ttl_seconds,
        )
        self.seed_if_empty()

    @classmethod
    def from_runtime_stores(
        cls,
        runtime_stores,
        *,
        allow_header_fallback: bool = True,
        seed_demo_users: bool = True,
        bootstrap_admin: OpsGraphBootstrapAdminSeed | None = None,
    ) -> "SqlAlchemyOpsGraphAuthService":
        return cls(
            runtime_stores.session_factory,
            runtime_stores.engine,
            allow_header_fallback=allow_header_fallback,
            seed_demo_users=seed_demo_users,
            bootstrap_admin=bootstrap_admin,
        )

    def build_authorizer(self) -> HybridOpsGraphAuthorizer:
        return HybridOpsGraphAuthorizer(self, allow_header_fallback=self.allow_header_fallback)

    def describe_runtime_auth_mode(self) -> dict[str, object]:
        mode = "demo_compatible" if self.allow_header_fallback or self.seed_demo_users else "strict"
        return {
            "mode": mode,
            "header_fallback_enabled": self.allow_header_fallback,
            "demo_seed_enabled": self.seed_demo_users,
            "bootstrap_admin_configured": self.bootstrap_admin is not None,
            "bootstrap_organization_slug": (
                self.bootstrap_admin.organization_slug.strip()
                if self.bootstrap_admin is not None
                else None
            ),
        }

    def create_session(
        self,
        command: SessionCreateCommand | dict[str, str],
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        if isinstance(command, dict):
            command = SessionCreateCommand.model_validate(command)
        with self.session_factory.begin() as session:
            organization = session.scalars(
                select(OrganizationRow).where(OrganizationRow.slug == command.organization_slug).limit(1)
            ).first()
            user = session.scalars(select(AppUserRow).where(AppUserRow.email == command.email).limit(1)).first()
            if (
                organization is None
                or user is None
                or user.status != "active"
                or not verify_password_pbkdf2(command.password, user.password_hash)
            ):
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Invalid email, password, or organization.",
                    status_code=401,
                )
            membership = session.scalars(
                select(OrganizationMembershipRow)
                .where(OrganizationMembershipRow.organization_id == organization.organization_id)
                .where(OrganizationMembershipRow.user_id == user.user_id)
                .where(OrganizationMembershipRow.status == "active")
                .limit(1)
            ).first()
            if membership is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Invalid email, password, or organization.",
                    status_code=401,
                )
            return self._issue_session(
                session,
                user=user,
                organization=organization,
                membership=membership,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    def refresh_session(
        self,
        refresh_token: str | None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        if refresh_token is None or not refresh_token.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_REFRESH_TOKEN_REQUIRED",
                message="Refresh token is required.",
                status_code=401,
            )
        refresh_token_hash = self._hash_refresh_token(refresh_token)
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            auth_session = session.scalars(
                select(AuthSessionRow)
                .where(AuthSessionRow.refresh_token_hash == refresh_token_hash)
                .limit(1)
            ).first()
            if auth_session is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Invalid refresh token.",
                    status_code=401,
                )
            if auth_session.revoked_at is not None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_SESSION_REVOKED",
                    message="Session has been revoked.",
                    status_code=401,
                )
            if auth_session.refresh_expires_at < now:
                auth_session.revoked_at = now
                auth_session.revoke_reason = "expired"
                auth_session.updated_at = now
                raise OpsGraphAuthorizationError(
                    code="AUTH_SESSION_EXPIRED",
                    message="Refresh token has expired.",
                    status_code=401,
                )
            user = session.get(AppUserRow, auth_session.user_id)
            organization = session.get(OrganizationRow, auth_session.organization_id)
            membership = session.scalars(
                select(OrganizationMembershipRow)
                .where(OrganizationMembershipRow.organization_id == auth_session.organization_id)
                .where(OrganizationMembershipRow.user_id == auth_session.user_id)
                .where(OrganizationMembershipRow.status == "active")
                .limit(1)
            ).first()
            if user is None or organization is None or membership is None or user.status != "active":
                auth_session.revoked_at = now
                auth_session.revoke_reason = "invalidated"
                auth_session.updated_at = now
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Refresh token is no longer valid.",
                    status_code=401,
                )
            rotated_issue = self._issue_session(
                session,
                user=user,
                organization=organization,
                membership=membership,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            auth_session.revoked_at = now
            auth_session.revoke_reason = "rotated"
            auth_session.replaced_by_session_id = rotated_issue.session_id
            auth_session.updated_at = now
            return rotated_issue

    def revoke_session(self, session_id: str | None) -> None:
        if session_id is None or not session_id.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_SESSION_REQUIRED",
                message="Session-backed authentication is required.",
                status_code=401,
            )
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            row = session.get(AuthSessionRow, session_id)
            if row is None:
                return
            if row.revoked_at is None:
                row.revoked_at = now
                row.revoke_reason = "user_logout"
                row.updated_at = now

    def authorize_access_token(
        self,
        token: str,
        *,
        required_role: str,
        organization_id: str | None = None,
    ) -> OpsGraphAccessContext:
        payload = self.access_token_codec.parse(token)
        session_id = str(payload["sid"])
        now = _utcnow_naive()
        with self.session_factory() as session:
            auth_session = session.get(AuthSessionRow, session_id)
            if auth_session is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Session does not exist.",
                    status_code=401,
                )
            if auth_session.revoked_at is not None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_SESSION_REVOKED",
                    message="Session has been revoked.",
                    status_code=401,
                )
            if auth_session.access_expires_at < now or auth_session.refresh_expires_at < now:
                raise OpsGraphAuthorizationError(
                    code="AUTH_SESSION_EXPIRED",
                    message="Session has expired.",
                    status_code=401,
                )
            normalized_role = _require_role(
                required_role=required_role,
                actual_role=str(payload["role"]),
            )
            if auth_session.user_id != str(payload["sub"]) or auth_session.organization_id != str(payload["org"]):
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Session context mismatch.",
                    status_code=401,
                )
            if organization_id is not None and organization_id.strip() and organization_id != auth_session.organization_id:
                raise OpsGraphAuthorizationError(
                    code="AUTH_FORBIDDEN",
                    message="Session organization does not match requested tenant context.",
                    status_code=403,
                )
            return OpsGraphAccessContext(
                organization_id=auth_session.organization_id,
                user_id=auth_session.user_id,
                role=normalized_role,
                session_id=auth_session.session_id,
            )

    def get_current_user(self, auth_context: OpsGraphAccessContext) -> CurrentUserResponse:
        if auth_context.session_id is None:
            raise OpsGraphAuthorizationError(
                code="AUTH_SESSION_REQUIRED",
                message="Session-backed authentication is required.",
                status_code=401,
            )
        with self.session_factory() as session:
            auth_session = session.get(AuthSessionRow, auth_context.session_id)
            user = session.get(AppUserRow, auth_context.user_id)
            organization = session.get(OrganizationRow, auth_context.organization_id)
            if auth_session is None or user is None or organization is None or auth_session.revoked_at is not None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Session is no longer valid.",
                    status_code=401,
                )
            memberships = self._list_memberships(session, user.user_id)
            return CurrentUserResponse(
                user=SessionUser(
                    user_id=user.user_id,
                    email=user.email,
                    display_name=user.display_name,
                ),
                active_organization=SessionOrganization(
                    organization_id=organization.organization_id,
                    slug=organization.slug,
                    name=organization.name,
                    status=organization.status,
                ),
                memberships=memberships,
            )

    def list_memberships(
        self,
        organization_id: str,
        *,
        status: str | None = None,
    ) -> list[ManagedMembershipSummary]:
        with self.session_factory() as session:
            organization = session.get(OrganizationRow, organization_id)
            if organization is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_ORGANIZATION_NOT_FOUND",
                    message="Organization does not exist.",
                    status_code=404,
                )
            stmt = (
                select(OrganizationMembershipRow)
                .where(OrganizationMembershipRow.organization_id == organization_id)
                .order_by(OrganizationMembershipRow.created_at.asc(), OrganizationMembershipRow.membership_id.asc())
            )
            if status is not None:
                stmt = stmt.where(OrganizationMembershipRow.status == status)
            rows = session.scalars(stmt).all()
            return [self._to_managed_membership(session, row, organization=organization) for row in rows]

    def provision_membership(
        self,
        organization_id: str,
        command: MembershipProvisionCommand | dict[str, str | None],
        *,
        actor_user_id: str | None = None,
    ) -> ManagedMembershipSummary:
        if isinstance(command, dict):
            command = MembershipProvisionCommand.model_validate(command)
        normalized_role = self._validate_membership_role(command.role)
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            organization = session.get(OrganizationRow, organization_id)
            if organization is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_ORGANIZATION_NOT_FOUND",
                    message="Organization does not exist.",
                    status_code=404,
                )
            email = command.email.strip().lower()
            user = session.scalars(select(AppUserRow).where(AppUserRow.email == email).limit(1)).first()
            if user is None:
                if command.password is None or not command.password.strip():
                    raise OpsGraphAuthorizationError(
                        code="AUTH_PASSWORD_REQUIRED",
                        message="Password is required when provisioning a new user.",
                        status_code=422,
                    )
                user = AppUserRow(
                    user_id=f"user-{uuid4().hex[:12]}",
                    email=email,
                    display_name=(command.display_name.strip() if command.display_name else _default_display_name(email)),
                    password_hash=hash_password_pbkdf2(command.password),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            else:
                if user.status != "active":
                    raise OpsGraphAuthorizationError(
                        code="AUTH_USER_INACTIVE",
                        message="User is not active.",
                        status_code=409,
                    )
                if command.display_name is not None and command.display_name.strip():
                    user.display_name = command.display_name.strip()
                    user.updated_at = now
            membership = session.scalars(
                select(OrganizationMembershipRow)
                .where(OrganizationMembershipRow.organization_id == organization_id)
                .where(OrganizationMembershipRow.user_id == user.user_id)
                .limit(1)
            ).first()
            if membership is None:
                membership = OrganizationMembershipRow(
                    membership_id=f"membership-{uuid4().hex[:12]}",
                    organization_id=organization_id,
                    user_id=user.user_id,
                    role=normalized_role,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                session.add(membership)
            else:
                if actor_user_id is not None and actor_user_id == membership.user_id and membership.status != "active":
                    raise OpsGraphAuthorizationError(
                        code="AUTH_SELF_LOCKOUT_FORBIDDEN",
                        message="Cannot reactivate or modify a suspended self membership from the same account context.",
                        status_code=409,
                    )
                role_changed = _normalize_role(membership.role) != normalized_role
                status_changed = membership.status != "active"
                membership.role = normalized_role
                membership.status = "active"
                membership.updated_at = now
                if role_changed or status_changed:
                    self._revoke_user_org_sessions(
                        session,
                        user_id=user.user_id,
                        organization_id=organization_id,
                        reason="membership_changed",
                        revoked_at=now,
                    )
            return self._to_managed_membership(session, membership, organization=organization, user=user)

    def update_membership(
        self,
        organization_id: str,
        membership_id: str,
        command: MembershipUpdateCommand | dict[str, str | None],
        *,
        actor_user_id: str | None = None,
    ) -> ManagedMembershipSummary:
        if isinstance(command, dict):
            command = MembershipUpdateCommand.model_validate(command)
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            organization = session.get(OrganizationRow, organization_id)
            membership = session.get(OrganizationMembershipRow, membership_id)
            if organization is None or membership is None or membership.organization_id != organization_id:
                raise OpsGraphAuthorizationError(
                    code="AUTH_MEMBERSHIP_NOT_FOUND",
                    message="Membership does not exist.",
                    status_code=404,
                )
            user = session.get(AppUserRow, membership.user_id)
            if user is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_MEMBERSHIP_NOT_FOUND",
                    message="Membership does not exist.",
                    status_code=404,
                )
            next_role = (
                self._validate_membership_role(command.role)
                if command.role is not None
                else _normalize_role(membership.role)
            )
            next_status = command.status or membership.status
            if actor_user_id is not None and actor_user_id == membership.user_id:
                if next_status != "active" or ROLE_PRIORITY[next_role] < ROLE_PRIORITY["product_admin"]:
                    raise OpsGraphAuthorizationError(
                        code="AUTH_SELF_LOCKOUT_FORBIDDEN",
                        message="Cannot remove your own product-admin access.",
                        status_code=409,
                    )
            role_changed = _normalize_role(membership.role) != next_role
            status_changed = membership.status != next_status
            if command.role is not None:
                membership.role = next_role
            if command.status is not None:
                membership.status = command.status
            if command.display_name is not None and command.display_name.strip():
                user.display_name = command.display_name.strip()
                user.updated_at = now
            membership.updated_at = now
            if role_changed or status_changed:
                self._revoke_user_org_sessions(
                    session,
                    user_id=membership.user_id,
                    organization_id=organization_id,
                    reason="membership_changed",
                    revoked_at=now,
                )
            return self._to_managed_membership(session, membership, organization=organization, user=user)

    def seed_if_empty(self) -> None:
        with self.session_factory.begin() as session:
            existing = session.scalar(select(OrganizationRow.organization_id).limit(1))
            if existing is not None:
                return
            now = _utcnow_naive()
            if self.bootstrap_admin is not None:
                self._seed_bootstrap_admin(session, now=now)
            elif self.seed_demo_users:
                self._seed_demo_users(session, now=now)

    def _issue_session(
        self,
        session: Session,
        *,
        user: AppUserRow,
        organization: OrganizationRow,
        membership: OrganizationMembershipRow,
        ip_address: str | None,
        user_agent: str | None,
    ) -> AuthSessionIssue:
        now = _utcnow_naive()
        session_id = f"auth-session-{uuid4().hex[:12]}"
        normalized_role = _normalize_role(membership.role)
        access_token, access_expires_at = self.access_token_codec.issue(
            session_id=session_id,
            user_id=user.user_id,
            organization_id=organization.organization_id,
            role=normalized_role,
        )
        refresh_token = token_urlsafe(48)
        refresh_expires_at = now + timedelta(days=self.refresh_ttl_days)
        session.add(
            AuthSessionRow(
                session_id=session_id,
                user_id=user.user_id,
                organization_id=organization.organization_id,
                role=membership.role,
                refresh_token_hash=self._hash_refresh_token(refresh_token),
                access_expires_at=_normalize_timestamp(access_expires_at),
                refresh_expires_at=refresh_expires_at,
                revoked_at=None,
                revoke_reason=None,
                replaced_by_session_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=now,
                updated_at=now,
            )
        )
        memberships = self._list_memberships(session, user.user_id)
        return AuthSessionIssue(
            session_id=session_id,
            response=SessionResponse(
                access_token=access_token,
                expires_at=access_expires_at,
                user=SessionUser(
                    user_id=user.user_id,
                    email=user.email,
                    display_name=user.display_name,
                ),
                active_organization=SessionOrganization(
                    organization_id=organization.organization_id,
                    slug=organization.slug,
                    name=organization.name,
                    status=organization.status,
                ),
                memberships=memberships,
            ),
            refresh_token=refresh_token,
        )

    def _list_memberships(self, session: Session, user_id: str) -> list[SessionMembership]:
        membership_rows = session.scalars(
            select(OrganizationMembershipRow)
            .where(OrganizationMembershipRow.user_id == user_id)
            .where(OrganizationMembershipRow.status == "active")
            .order_by(OrganizationMembershipRow.organization_id.asc())
        ).all()
        organization_ids = [row.organization_id for row in membership_rows]
        organizations = {
            row.organization_id: row
            for row in session.scalars(
                select(OrganizationRow).where(OrganizationRow.organization_id.in_(organization_ids))
            ).all()
        }
        return [
            SessionMembership(
                organization_id=row.organization_id,
                organization_slug=organizations[row.organization_id].slug,
                organization_name=organizations[row.organization_id].name,
                role=row.role,
            )
            for row in membership_rows
            if row.organization_id in organizations
        ]

    @staticmethod
    def _seed_demo_users(session: Session, *, now: datetime) -> None:
        session.add(
            OrganizationRow(
                organization_id="org-1",
                slug="acme",
                name="Acme",
                status="active",
                settings_json={},
                created_at=now,
                updated_at=now,
            )
        )
        seeded_users = (
            ("user-viewer-1", "viewer@example.com", "Ops Viewer", "viewer"),
            ("user-operator-1", "operator@example.com", "Ops Operator", "operator"),
            ("user-admin-1", "admin@example.com", "Ops Admin", "org_admin"),
        )
        for user_id, email, display_name, role in seeded_users:
            session.add(
                AppUserRow(
                    user_id=user_id,
                    email=email,
                    display_name=display_name,
                    password_hash=hash_password_pbkdf2("opsgraph-demo"),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                OrganizationMembershipRow(
                    membership_id=f"membership-{user_id}",
                    organization_id="org-1",
                    user_id=user_id,
                    role=role,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )

    def _seed_bootstrap_admin(self, session: Session, *, now: datetime) -> None:
        assert self.bootstrap_admin is not None
        email = self.bootstrap_admin.email.strip().lower()
        password = self.bootstrap_admin.password
        if not email or not password:
            raise ValueError("Bootstrap admin email and password must both be configured.")
        display_name = self.bootstrap_admin.display_name.strip() or "OpsGraph Admin"
        organization_slug = self.bootstrap_admin.organization_slug.strip() or "opsgraph"
        organization_name = self.bootstrap_admin.organization_name.strip() or "OpsGraph"
        session.add(
            OrganizationRow(
                organization_id="org-bootstrap-1",
                slug=organization_slug,
                name=organization_name,
                status="active",
                settings_json={"bootstrap_admin_seeded": True},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AppUserRow(
                user_id="user-bootstrap-admin-1",
                email=email,
                display_name=display_name,
                password_hash=hash_password_pbkdf2(password),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            OrganizationMembershipRow(
                membership_id="membership-user-bootstrap-admin-1",
                organization_id="org-bootstrap-1",
                user_id="user-bootstrap-admin-1",
                role="org_admin",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _validate_membership_role(role: str) -> str:
        normalized_role = _normalize_role(role)
        if normalized_role not in ROLE_PRIORITY:
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_ROLE",
                message=f"Unknown user role: {role}",
                status_code=400,
            )
        return normalized_role

    def _to_managed_membership(
        self,
        session: Session,
        membership: OrganizationMembershipRow,
        *,
        organization: OrganizationRow | None = None,
        user: AppUserRow | None = None,
    ) -> ManagedMembershipSummary:
        organization = organization or session.get(OrganizationRow, membership.organization_id)
        user = user or session.get(AppUserRow, membership.user_id)
        if organization is None or user is None:
            raise OpsGraphAuthorizationError(
                code="AUTH_MEMBERSHIP_NOT_FOUND",
                message="Membership does not exist.",
                status_code=404,
            )
        return ManagedMembershipSummary(
            membership_id=membership.membership_id,
            organization_id=organization.organization_id,
            organization_slug=organization.slug,
            organization_name=organization.name,
            user=ManagedUserSummary(
                user_id=user.user_id,
                email=user.email,
                display_name=user.display_name,
                status=user.status,
            ),
            role=_normalize_role(membership.role),
            status=membership.status,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )

    @staticmethod
    def _revoke_user_org_sessions(
        session: Session,
        *,
        user_id: str,
        organization_id: str,
        reason: str,
        revoked_at: datetime,
    ) -> None:
        rows = session.scalars(
            select(AuthSessionRow)
            .where(AuthSessionRow.user_id == user_id)
            .where(AuthSessionRow.organization_id == organization_id)
            .where(AuthSessionRow.revoked_at.is_(None))
        ).all()
        for row in rows:
            row.revoked_at = revoked_at
            row.revoke_reason = reason
            row.updated_at = revoked_at

    @staticmethod
    def _hash_refresh_token(refresh_token: str) -> str:
        return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


class _SharedPlatformTokenCodecProxy:
    def looks_issued_token(self, token: str) -> bool:
        encoded_payload, separator, encoded_signature = token.partition(".")
        if separator != "." or not encoded_payload or not encoded_signature:
            return False
        try:
            payload = json.loads(_urlsafe_b64decode(encoded_payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            return False
        return (
            isinstance(payload, dict)
            and {"session_id", "user_id", "organization_id", "role", "exp"}.issubset(payload)
        )


class SharedPlatformBackedOpsGraphAuthService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: Engine,
        *,
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        refresh_ttl_days: int = DEFAULT_REFRESH_TTL_DAYS,
        allow_header_fallback: bool = True,
        seed_demo_users: bool = True,
        bootstrap_admin: OpsGraphBootstrapAdminSeed | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_days = refresh_ttl_days
        self.allow_header_fallback = allow_header_fallback
        self.seed_demo_users = seed_demo_users
        self.bootstrap_admin = bootstrap_admin
        self._shared_platform = load_shared_agent_platform()
        self._shared_error_type = getattr(self._shared_platform, "SharedAuthorizationError", Exception)
        seed_organizations, seed_user_memberships = self._build_platform_seed_data()
        self.shared_auth_service = self._shared_platform.SqlAlchemyPlatformAuthService(
            session_factory,
            engine,
            auth_secret=os.getenv("OPSGRAPH_AUTH_SECRET") or DEFAULT_AUTH_SECRET,
            access_ttl_seconds=access_ttl_seconds,
            refresh_ttl_days=refresh_ttl_days,
            role_priority=ROLE_PRIORITY,
            role_aliases=ROLE_ALIASES,
            error_type=self._shared_error_type,
            seed_organizations=seed_organizations,
            seed_user_memberships=seed_user_memberships,
        )
        self.access_token_codec = _SharedPlatformTokenCodecProxy()

    @classmethod
    def from_runtime_stores(
        cls,
        runtime_stores,
        *,
        allow_header_fallback: bool = True,
        seed_demo_users: bool = True,
        bootstrap_admin: OpsGraphBootstrapAdminSeed | None = None,
    ) -> "SharedPlatformBackedOpsGraphAuthService":
        return cls(
            runtime_stores.session_factory,
            runtime_stores.engine,
            allow_header_fallback=allow_header_fallback,
            seed_demo_users=seed_demo_users,
            bootstrap_admin=bootstrap_admin,
        )

    def build_authorizer(self) -> HybridOpsGraphAuthorizer:
        return HybridOpsGraphAuthorizer(self, allow_header_fallback=self.allow_header_fallback)

    def describe_runtime_auth_mode(self) -> dict[str, object]:
        mode = "demo_compatible" if self.allow_header_fallback or self.seed_demo_users else "strict"
        return {
            "mode": mode,
            "source": "shared_delegated",
            "header_fallback_enabled": self.allow_header_fallback,
            "demo_seed_enabled": self.seed_demo_users,
            "bootstrap_admin_configured": self.bootstrap_admin is not None,
            "bootstrap_organization_slug": (
                self.bootstrap_admin.organization_slug.strip()
                if self.bootstrap_admin is not None
                else None
            ),
        }

    def create_session(
        self,
        command: SessionCreateCommand | dict[str, str],
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        try:
            shared_issue = self.shared_auth_service.create_session(
                command,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as exc:
            self._raise_translated(exc)
        session_id = self._extract_session_id(shared_issue.response.access_token)
        return AuthSessionIssue(
            session_id=session_id,
            response=self._to_session_response(shared_issue.response),
            refresh_token=shared_issue.refresh_token,
        )

    def refresh_session(
        self,
        refresh_token: str | None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        if refresh_token is None or not refresh_token.strip():
            raise OpsGraphAuthorizationError(
                code="AUTH_REFRESH_TOKEN_REQUIRED",
                message="Refresh token is required.",
                status_code=401,
            )
        try:
            shared_issue = self.shared_auth_service.refresh_session(
                refresh_token,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as exc:
            self._raise_translated(exc)
        session_id = self._extract_session_id(shared_issue.response.access_token)
        return AuthSessionIssue(
            session_id=session_id,
            response=self._to_session_response(shared_issue.response),
            refresh_token=shared_issue.refresh_token,
        )

    def revoke_session(self, session_id: str | None) -> None:
        try:
            self.shared_auth_service.revoke_session(session_id)
        except Exception as exc:
            self._raise_translated(exc)

    def authorize_access_token(
        self,
        token: str,
        *,
        required_role: str,
        organization_id: str | None = None,
    ) -> OpsGraphAccessContext:
        try:
            access_context = self.shared_auth_service.authorize_access_token(
                required_role=required_role,
                authorization=f"Bearer {token}",
                organization_id=organization_id,
            )
        except Exception as exc:
            self._raise_translated(exc)
        return OpsGraphAccessContext(
            organization_id=access_context.organization_id,
            user_id=access_context.user_id,
            role=_normalize_role(access_context.role),
            session_id=access_context.session_id,
        )

    def get_current_user(self, auth_context: OpsGraphAccessContext) -> CurrentUserResponse:
        if auth_context.session_id is None:
            raise OpsGraphAuthorizationError(
                code="AUTH_SESSION_REQUIRED",
                message="Session-backed authentication is required.",
                status_code=401,
            )
        try:
            current_user = self.shared_auth_service.get_current_user(
                self._shared_platform.AuthAccessContext(
                    organization_id=auth_context.organization_id,
                    user_id=auth_context.user_id,
                    role=auth_context.role,
                    session_id=auth_context.session_id,
                )
            )
        except Exception as exc:
            self._raise_translated(exc)
        return self._to_current_user_response(current_user)

    def list_memberships(
        self,
        organization_id: str,
        *,
        status: str | None = None,
    ) -> list[ManagedMembershipSummary]:
        with self.session_factory() as session:
            organization = session.get(self._shared_platform.OrganizationRow, organization_id)
            if organization is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_ORGANIZATION_NOT_FOUND",
                    message="Organization does not exist.",
                    status_code=404,
                )
            stmt = (
                select(self._shared_platform.OrganizationMembershipRow)
                .where(self._shared_platform.OrganizationMembershipRow.organization_id == organization_id)
                .order_by(
                    self._shared_platform.OrganizationMembershipRow.created_at.asc(),
                    self._shared_platform.OrganizationMembershipRow.membership_id.asc(),
                )
            )
            if status is not None:
                stmt = stmt.where(self._shared_platform.OrganizationMembershipRow.status == status)
            rows = session.scalars(stmt).all()
            return [self._to_managed_membership(session, row, organization=organization) for row in rows]

    def provision_membership(
        self,
        organization_id: str,
        command: MembershipProvisionCommand | dict[str, str | None],
        *,
        actor_user_id: str | None = None,
    ) -> ManagedMembershipSummary:
        if isinstance(command, dict):
            command = MembershipProvisionCommand.model_validate(command)
        normalized_role = self._validate_membership_role(command.role)
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            organization = session.get(self._shared_platform.OrganizationRow, organization_id)
            if organization is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_ORGANIZATION_NOT_FOUND",
                    message="Organization does not exist.",
                    status_code=404,
                )
            email = command.email.strip().lower()
            user = session.scalars(
                select(self._shared_platform.AppUserRow)
                .where(self._shared_platform.AppUserRow.email == email)
                .limit(1)
            ).first()
            if user is None:
                if command.password is None or not command.password.strip():
                    raise OpsGraphAuthorizationError(
                        code="AUTH_PASSWORD_REQUIRED",
                        message="Password is required when provisioning a new user.",
                        status_code=422,
                    )
                user = self._shared_platform.AppUserRow(
                    user_id=f"user-{uuid4().hex[:12]}",
                    email=email,
                    display_name=(command.display_name.strip() if command.display_name else _default_display_name(email)),
                    password_hash=self._shared_platform.hash_password_pbkdf2(command.password),
                    status="active",
                    last_login_at=None,
                    profile_json={},
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            else:
                if user.status != "active":
                    raise OpsGraphAuthorizationError(
                        code="AUTH_USER_INACTIVE",
                        message="User is not active.",
                        status_code=409,
                    )
                if command.display_name is not None and command.display_name.strip():
                    user.display_name = command.display_name.strip()
                    user.updated_at = now
            membership = session.scalars(
                select(self._shared_platform.OrganizationMembershipRow)
                .where(self._shared_platform.OrganizationMembershipRow.organization_id == organization_id)
                .where(self._shared_platform.OrganizationMembershipRow.user_id == user.user_id)
                .limit(1)
            ).first()
            if membership is None:
                membership = self._shared_platform.OrganizationMembershipRow(
                    membership_id=f"membership-{uuid4().hex[:12]}",
                    organization_id=organization_id,
                    user_id=user.user_id,
                    role=normalized_role,
                    status="active",
                    invited_by_user_id=actor_user_id,
                    joined_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(membership)
            else:
                if actor_user_id is not None and actor_user_id == membership.user_id and membership.status != "active":
                    raise OpsGraphAuthorizationError(
                        code="AUTH_SELF_LOCKOUT_FORBIDDEN",
                        message="Cannot reactivate or modify a suspended self membership from the same account context.",
                        status_code=409,
                    )
                role_changed = _normalize_role(membership.role) != normalized_role
                status_changed = membership.status != "active"
                membership.role = normalized_role
                membership.status = "active"
                membership.updated_at = now
                membership.invited_by_user_id = actor_user_id
                if membership.joined_at is None:
                    membership.joined_at = now
                if role_changed or status_changed:
                    self._revoke_user_org_sessions(
                        session,
                        user_id=user.user_id,
                        organization_id=organization_id,
                        revoked_at=now,
                    )
            return self._to_managed_membership(session, membership, organization=organization, user=user)

    def update_membership(
        self,
        organization_id: str,
        membership_id: str,
        command: MembershipUpdateCommand | dict[str, str | None],
        *,
        actor_user_id: str | None = None,
    ) -> ManagedMembershipSummary:
        if isinstance(command, dict):
            command = MembershipUpdateCommand.model_validate(command)
        now = _utcnow_naive()
        with self.session_factory.begin() as session:
            organization = session.get(self._shared_platform.OrganizationRow, organization_id)
            membership = session.get(self._shared_platform.OrganizationMembershipRow, membership_id)
            if organization is None or membership is None or membership.organization_id != organization_id:
                raise OpsGraphAuthorizationError(
                    code="AUTH_MEMBERSHIP_NOT_FOUND",
                    message="Membership does not exist.",
                    status_code=404,
                )
            user = session.get(self._shared_platform.AppUserRow, membership.user_id)
            if user is None:
                raise OpsGraphAuthorizationError(
                    code="AUTH_MEMBERSHIP_NOT_FOUND",
                    message="Membership does not exist.",
                    status_code=404,
                )
            next_role = (
                self._validate_membership_role(command.role)
                if command.role is not None
                else _normalize_role(membership.role)
            )
            next_status = command.status or membership.status
            if actor_user_id is not None and actor_user_id == membership.user_id:
                if next_status != "active" or ROLE_PRIORITY[next_role] < ROLE_PRIORITY["product_admin"]:
                    raise OpsGraphAuthorizationError(
                        code="AUTH_SELF_LOCKOUT_FORBIDDEN",
                        message="Cannot remove your own product-admin access.",
                        status_code=409,
                    )
            role_changed = _normalize_role(membership.role) != next_role
            status_changed = membership.status != next_status
            if command.role is not None:
                membership.role = next_role
            if command.status is not None:
                membership.status = command.status
            if command.display_name is not None and command.display_name.strip():
                user.display_name = command.display_name.strip()
                user.updated_at = now
            membership.updated_at = now
            if role_changed or status_changed:
                self._revoke_user_org_sessions(
                    session,
                    user_id=membership.user_id,
                    organization_id=organization_id,
                    revoked_at=now,
                )
            return self._to_managed_membership(session, membership, organization=organization, user=user)

    def _build_platform_seed_data(
        self,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        if self.bootstrap_admin is not None:
            bootstrap = self.bootstrap_admin
            organization_slug = bootstrap.organization_slug.strip() or "opsgraph"
            organization_name = bootstrap.organization_name.strip() or "OpsGraph"
            return (
                (
                    self._shared_platform.SeedOrganization(
                        organization_id="org-bootstrap-1",
                        name=organization_name,
                        slug=organization_slug,
                        settings_json={"bootstrap_admin_seeded": True},
                    ),
                ),
                (
                    self._shared_platform.SeedUserMembership(
                        user_id="user-bootstrap-admin-1",
                        email=bootstrap.email.strip().lower(),
                        display_name=bootstrap.display_name.strip() or "OpsGraph Admin",
                        password=bootstrap.password,
                        organization_id="org-bootstrap-1",
                        role="org_admin",
                    ),
                ),
            )
        if not self.seed_demo_users:
            return (), ()
        return (
            (
                self._shared_platform.SeedOrganization(
                    organization_id="org-1",
                    name="Acme",
                    slug="acme",
                    settings_json={},
                ),
            ),
            (
                self._shared_platform.SeedUserMembership(
                    user_id="user-viewer-1",
                    email="viewer@example.com",
                    display_name="Ops Viewer",
                    password="opsgraph-demo",
                    organization_id="org-1",
                    role="viewer",
                ),
                self._shared_platform.SeedUserMembership(
                    user_id="user-operator-1",
                    email="operator@example.com",
                    display_name="Ops Operator",
                    password="opsgraph-demo",
                    organization_id="org-1",
                    role="operator",
                ),
                self._shared_platform.SeedUserMembership(
                    user_id="user-admin-1",
                    email="admin@example.com",
                    display_name="Ops Admin",
                    password="opsgraph-demo",
                    organization_id="org-1",
                    role="org_admin",
                ),
            ),
        )

    def _to_session_response(self, response) -> SessionResponse:
        organization_rows = self._organization_rows(
            [response.active_organization.id]
            + [membership.organization_id for membership in response.memberships]
        )
        active_organization = organization_rows.get(response.active_organization.id)
        return SessionResponse(
            access_token=response.access_token,
            expires_at=response.expires_at,
            user=SessionUser(
                user_id=response.user.id,
                email=response.user.email,
                display_name=response.user.display_name,
            ),
            active_organization=SessionOrganization(
                organization_id=response.active_organization.id,
                slug=(
                    active_organization.slug
                    if active_organization is not None
                    else response.active_organization.slug
                ),
                name=response.active_organization.name,
                status=active_organization.status if active_organization is not None else "active",
            ),
            memberships=[
                SessionMembership(
                    organization_id=membership.organization_id,
                    organization_slug=(
                        organization_rows[membership.organization_id].slug
                        if membership.organization_id in organization_rows
                        else ""
                    ),
                    organization_name=(
                        organization_rows[membership.organization_id].name
                        if membership.organization_id in organization_rows
                        else membership.organization_id
                    ),
                    role=membership.role,
                )
                for membership in response.memberships
            ],
        )

    def _to_current_user_response(self, response) -> CurrentUserResponse:
        organization_rows = self._organization_rows(
            [response.active_organization.id]
            + [membership.organization_id for membership in response.memberships]
        )
        active_organization = organization_rows.get(response.active_organization.id)
        return CurrentUserResponse(
            user=SessionUser(
                user_id=response.user.id,
                email=response.user.email,
                display_name=response.user.display_name,
            ),
            active_organization=SessionOrganization(
                organization_id=response.active_organization.id,
                slug=(
                    active_organization.slug
                    if active_organization is not None
                    else response.active_organization.slug
                ),
                name=response.active_organization.name,
                status=active_organization.status if active_organization is not None else "active",
            ),
            memberships=[
                SessionMembership(
                    organization_id=membership.organization_id,
                    organization_slug=(
                        organization_rows[membership.organization_id].slug
                        if membership.organization_id in organization_rows
                        else ""
                    ),
                    organization_name=(
                        organization_rows[membership.organization_id].name
                        if membership.organization_id in organization_rows
                        else membership.organization_id
                    ),
                    role=membership.role,
                )
                for membership in response.memberships
            ],
        )

    def _organization_rows(self, organization_ids: list[str]) -> dict[str, Any]:
        normalized_ids = [item for item in dict.fromkeys(organization_ids) if item]
        if not normalized_ids:
            return {}
        with self.session_factory() as session:
            return {
                row.organization_id: row
                for row in session.scalars(
                    select(self._shared_platform.OrganizationRow).where(
                        self._shared_platform.OrganizationRow.organization_id.in_(normalized_ids)
                    )
                ).all()
            }

    def _to_managed_membership(
        self,
        session: Session,
        membership,
        *,
        organization=None,
        user=None,
    ) -> ManagedMembershipSummary:
        organization = organization or session.get(self._shared_platform.OrganizationRow, membership.organization_id)
        user = user or session.get(self._shared_platform.AppUserRow, membership.user_id)
        if organization is None or user is None:
            raise OpsGraphAuthorizationError(
                code="AUTH_MEMBERSHIP_NOT_FOUND",
                message="Membership does not exist.",
                status_code=404,
            )
        return ManagedMembershipSummary(
            membership_id=membership.membership_id,
            organization_id=organization.organization_id,
            organization_slug=organization.slug,
            organization_name=organization.name,
            user=ManagedUserSummary(
                user_id=user.user_id,
                email=user.email,
                display_name=user.display_name,
                status=user.status,
            ),
            role=_normalize_role(membership.role),
            status=membership.status,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )

    @staticmethod
    def _validate_membership_role(role: str) -> str:
        normalized_role = _normalize_role(role)
        if normalized_role not in ROLE_PRIORITY:
            raise OpsGraphAuthorizationError(
                code="AUTH_INVALID_ROLE",
                message=f"Unknown user role: {role}",
                status_code=400,
            )
        return normalized_role

    def _raise_translated(self, exc: Exception) -> None:
        if isinstance(exc, OpsGraphAuthorizationError):
            raise exc
        if isinstance(exc, self._shared_error_type):
            raise self._translate_shared_error(exc) from exc
        raise exc

    def _translate_shared_error(self, exc: Exception) -> OpsGraphAuthorizationError:
        code = str(getattr(exc, "code", "AUTH_INVALID_CREDENTIALS"))
        message = str(getattr(exc, "message", str(exc)))
        status_code = int(getattr(exc, "status_code", 401))
        if code == "AUTH_ORGANIZATION_NOT_ACCESSIBLE":
            return OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid email, password, or organization.",
                status_code=401,
            )
        if code in {"AUTH_REFRESH_EXPIRED", "AUTH_ACCESS_EXPIRED"}:
            return OpsGraphAuthorizationError(
                code="AUTH_SESSION_EXPIRED",
                message="Session has expired.",
                status_code=401,
            )
        if code == "AUTH_CONTEXT_INVALID":
            return OpsGraphAuthorizationError(
                code="AUTH_FORBIDDEN",
                message="Session organization does not match requested tenant context.",
                status_code=403,
            )
        if code == "AUTH_USER_DISABLED":
            return OpsGraphAuthorizationError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid email, password, or organization.",
                status_code=401,
            )
        return OpsGraphAuthorizationError(
            code=code,
            message=message,
            status_code=status_code,
        )

    def _extract_session_id(self, access_token: str) -> str:
        try:
            payload = self.shared_auth_service.token_codec.parse(access_token)
        except Exception:
            return ""
        return str(payload.get("session_id") or "")

    def _revoke_user_org_sessions(
        self,
        session: Session,
        *,
        user_id: str,
        organization_id: str,
        revoked_at: datetime,
    ) -> None:
        rows = session.scalars(
            select(self._shared_platform.AuthSessionRow)
            .where(self._shared_platform.AuthSessionRow.user_id == user_id)
            .where(self._shared_platform.AuthSessionRow.organization_id == organization_id)
            .where(self._shared_platform.AuthSessionRow.session_status == "active")
        ).all()
        for row in rows:
            row.session_status = "revoked"
            row.updated_at = revoked_at
