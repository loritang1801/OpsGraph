from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping, Protocol, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .auth_primitives import (
    AccessTokenCodec,
    env_secret,
    extract_bearer_token,
    hash_password_pbkdf2,
    hash_token_sha256,
    normalize_role,
    require_role,
    verify_password_pbkdf2,
)
from .errors import SharedAuthorizationError

DEFAULT_AUTH_SECRET = "platform-dev-secret"
DEFAULT_ACCESS_TTL_SECONDS = 60 * 60
DEFAULT_REFRESH_TTL_DAYS = 30


@dataclass(slots=True, frozen=True)
class AuthAccessContext:
    organization_id: str
    user_id: str
    role: str
    session_id: str | None = None


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionUser(AuthModel):
    id: str
    email: str
    display_name: str


class SessionOrganization(AuthModel):
    id: str
    name: str
    slug: str


class SessionMembership(AuthModel):
    organization_id: str
    role: str
    status: str


class SessionResponse(AuthModel):
    user: SessionUser
    active_organization: SessionOrganization
    memberships: list[SessionMembership]
    access_token: str
    expires_at: datetime


class CurrentUserResponse(AuthModel):
    user: SessionUser
    active_organization: SessionOrganization
    memberships: list[SessionMembership]


class SessionCreateCommand(AuthModel):
    email: str
    password: str
    organization_slug: str


@dataclass(slots=True, frozen=True)
class AuthSessionIssue:
    response: SessionResponse
    refresh_token: str


@dataclass(slots=True, frozen=True)
class SeedOrganization:
    organization_id: str
    name: str
    slug: str
    status: str = "active"
    settings_json: dict[str, object] | None = None


@dataclass(slots=True, frozen=True)
class SeedUserMembership:
    user_id: str
    email: str
    display_name: str
    password: str
    organization_id: str
    role: str
    user_status: str = "active"
    membership_status: str = "active"


class RoleAuthorizer(Protocol):
    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> AuthAccessContext: ...


class HeaderRoleAuthorizer:
    def __init__(
        self,
        *,
        default_role: str = "viewer",
        role_priority: Mapping[str, int],
        role_aliases: Mapping[str, str] | None = None,
        error_type: type[Exception] = SharedAuthorizationError,
    ) -> None:
        self.role_priority = dict(role_priority)
        self.role_aliases = dict(role_aliases or {})
        self.error_type = error_type
        self.default_role = self._normalize_role(default_role)

    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> AuthAccessContext:
        required = self._normalize_role(required_role)
        org_id = (organization_id or "").strip()
        if not org_id:
            raise self.error_type(
                code="TENANT_CONTEXT_REQUIRED",
                message="X-Organization-Id header is required.",
                status_code=400,
            )
        extract_bearer_token(authorization, error_type=self.error_type)
        role = require_role(
            required_role=required,
            actual_role=user_role or self.default_role,
            role_priority=self.role_priority,
            role_aliases=self.role_aliases,
            error_type=self.error_type,
        )
        normalized_user_id = (user_id or "demo-user").strip() or "demo-user"
        return AuthAccessContext(
            organization_id=org_id,
            user_id=normalized_user_id,
            role=role,
        )

    def _normalize_role(self, role: str) -> str:
        return normalize_role(
            role,
            role_priority=self.role_priority,
            role_aliases=self.role_aliases,
            error_type=self.error_type,
        )


class SessionTokenAuthorizer:
    def __init__(self, auth_service: "SqlAlchemyPlatformAuthService") -> None:
        self.auth_service = auth_service

    def authorize(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
        user_id: str | None = None,
        user_role: str | None = None,
    ) -> AuthAccessContext:
        del user_id
        del user_role
        return self.auth_service.authorize_access_token(
            required_role=required_role,
            authorization=authorization,
            organization_id=organization_id,
        )


class AuthBase(DeclarativeBase):
    pass


class OrganizationRow(AuthBase):
    __tablename__ = "platform_organization"

    organization_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30))
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class AppUserRow(AuthBase):
    __tablename__ = "platform_app_user"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class OrganizationMembershipRow(AuthBase):
    __tablename__ = "platform_organization_membership"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_platform_membership_org_user"),
    )

    membership_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    invited_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class AuthSessionRow(AuthBase):
    __tablename__ = "platform_auth_session"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    organization_id: Mapped[str] = mapped_column(String(255), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    session_status: Mapped[str] = mapped_column(String(30), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


def create_auth_tables(engine: Engine) -> None:
    AuthBase.metadata.create_all(engine)


class SqlAlchemyPlatformAuthService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: Engine,
        *,
        auth_secret: str | None = None,
        auth_secret_env_name: str = "PLATFORM_AUTH_SECRET",
        default_auth_secret: str = DEFAULT_AUTH_SECRET,
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        refresh_ttl_days: int = DEFAULT_REFRESH_TTL_DAYS,
        role_priority: Mapping[str, int],
        role_aliases: Mapping[str, str] | None = None,
        error_type: type[Exception] = SharedAuthorizationError,
        seed_organizations: Sequence[SeedOrganization] = (),
        seed_user_memberships: Sequence[SeedUserMembership] = (),
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.refresh_ttl_days = refresh_ttl_days
        self.role_priority = dict(role_priority)
        self.role_aliases = dict(role_aliases or {})
        self.error_type = error_type
        self.seed_organizations = tuple(seed_organizations)
        self.seed_user_memberships = tuple(seed_user_memberships)
        self.token_codec = AccessTokenCodec(
            auth_secret or env_secret(auth_secret_env_name, default=default_auth_secret),
            ttl_seconds=access_ttl_seconds,
            error_type=error_type,
        )
        create_auth_tables(engine)
        self.seed_if_empty()

    @classmethod
    def from_runtime_stores(cls, runtime_stores, **kwargs) -> "SqlAlchemyPlatformAuthService":
        return cls(runtime_stores.session_factory, runtime_stores.engine, **kwargs)

    def build_authorizer(self) -> SessionTokenAuthorizer:
        return SessionTokenAuthorizer(self)

    def seed_if_empty(self) -> None:
        with self.session_factory.begin() as session:
            existing = session.scalar(select(OrganizationRow.organization_id).limit(1))
            if existing is not None:
                return
            now = self._utcnow_naive()
            for organization in self.seed_organizations:
                session.add(
                    OrganizationRow(
                        organization_id=organization.organization_id,
                        name=organization.name,
                        slug=organization.slug,
                        status=organization.status,
                        settings_json=dict(organization.settings_json or {}),
                        created_at=now,
                        updated_at=now,
                    )
                )
            for seeded_user in self.seed_user_memberships:
                session.add(
                    AppUserRow(
                        user_id=seeded_user.user_id,
                        email=seeded_user.email,
                        display_name=seeded_user.display_name,
                        password_hash=self._hash_password(seeded_user.password),
                        status=seeded_user.user_status,
                        last_login_at=None,
                        profile_json={},
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    OrganizationMembershipRow(
                        membership_id=f"membership-{uuid4().hex[:10]}",
                        organization_id=seeded_user.organization_id,
                        user_id=seeded_user.user_id,
                        role=seeded_user.role,
                        status=seeded_user.membership_status,
                        invited_by_user_id=None,
                        joined_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def create_session(
        self,
        command: SessionCreateCommand | dict[str, str],
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        if isinstance(command, dict):
            command = SessionCreateCommand.model_validate(command)
        email = command.email.strip().lower()
        organization_slug = command.organization_slug.strip().lower()
        with self.session_factory.begin() as session:
            organization_row = session.scalars(
                select(OrganizationRow)
                .where(OrganizationRow.slug == organization_slug)
                .where(OrganizationRow.status == "active")
                .limit(1)
            ).first()
            if organization_row is None:
                raise self.error_type(
                    code="AUTH_ORGANIZATION_NOT_ACCESSIBLE",
                    message="Organization is not accessible.",
                    status_code=403,
                )
            user_row = session.scalars(
                select(AppUserRow).where(AppUserRow.email == email).limit(1)
            ).first()
            if user_row is None or not self._verify_password(command.password, user_row.password_hash):
                raise self.error_type(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Invalid credentials.",
                    status_code=401,
                )
            if user_row.status != "active":
                raise self.error_type(
                    code="AUTH_USER_DISABLED",
                    message="User is not active.",
                    status_code=403,
                )
            membership_row = self._load_membership(
                session,
                user_id=user_row.user_id,
                organization_id=organization_row.organization_id,
            )
            if membership_row is None:
                raise self.error_type(
                    code="AUTH_ORGANIZATION_NOT_ACCESSIBLE",
                    message="User is not a member of the requested organization.",
                    status_code=403,
                )
            now = self._utcnow_naive()
            session_row = AuthSessionRow(
                session_id=f"auth-session-{uuid4().hex[:12]}",
                user_id=user_row.user_id,
                organization_id=organization_row.organization_id,
                refresh_token_hash="",
                session_status="active",
                expires_at=now + timedelta(days=self.refresh_ttl_days),
                last_seen_at=now,
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=now,
                updated_at=now,
            )
            refresh_token = secrets.token_urlsafe(32)
            session_row.refresh_token_hash = self._hash_refresh_token(refresh_token)
            user_row.last_login_at = now
            user_row.updated_at = now
            session.add(session_row)
            memberships = self._list_memberships(session, user_id=user_row.user_id)
            response = self._build_session_response(
                user_row=user_row,
                organization_row=organization_row,
                memberships=memberships,
                session_id=session_row.session_id,
                role=membership_row.role,
            )
        return AuthSessionIssue(response=response, refresh_token=refresh_token)

    def refresh_session(
        self,
        refresh_token: str | None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSessionIssue:
        normalized_refresh_token = (refresh_token or "").strip()
        if not normalized_refresh_token:
            raise self.error_type(
                code="AUTH_REFRESH_EXPIRED",
                message="Refresh session is missing or expired.",
                status_code=401,
            )
        refresh_token_hash = self._hash_refresh_token(normalized_refresh_token)
        with self.session_factory.begin() as session:
            session_row = session.scalars(
                select(AuthSessionRow)
                .where(AuthSessionRow.refresh_token_hash == refresh_token_hash)
                .limit(1)
            ).first()
            if session_row is None:
                raise self.error_type(
                    code="AUTH_REFRESH_EXPIRED",
                    message="Refresh session has expired.",
                    status_code=401,
                )
            if session_row.session_status == "revoked":
                raise self.error_type(
                    code="AUTH_SESSION_REVOKED",
                    message="Refresh session has been revoked.",
                    status_code=401,
                )
            now = self._utcnow_naive()
            if session_row.expires_at <= now:
                session_row.session_status = "expired"
                session_row.updated_at = now
                raise self.error_type(
                    code="AUTH_REFRESH_EXPIRED",
                    message="Refresh session has expired.",
                    status_code=401,
                )
            user_row = session.get(AppUserRow, session_row.user_id)
            organization_row = session.get(OrganizationRow, session_row.organization_id)
            if user_row is None or organization_row is None:
                raise self.error_type(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Session principal could not be loaded.",
                    status_code=401,
                )
            membership_row = self._load_membership(
                session,
                user_id=user_row.user_id,
                organization_id=organization_row.organization_id,
            )
            if membership_row is None:
                raise self.error_type(
                    code="AUTH_ORGANIZATION_NOT_ACCESSIBLE",
                    message="User is not an active organization member.",
                    status_code=403,
                )
            rotated_refresh_token = secrets.token_urlsafe(32)
            session_row.refresh_token_hash = self._hash_refresh_token(rotated_refresh_token)
            session_row.last_seen_at = now
            session_row.updated_at = now
            session_row.ip_address = ip_address
            session_row.user_agent = user_agent
            memberships = self._list_memberships(session, user_id=user_row.user_id)
            response = self._build_session_response(
                user_row=user_row,
                organization_row=organization_row,
                memberships=memberships,
                session_id=session_row.session_id,
                role=membership_row.role,
            )
        return AuthSessionIssue(response=response, refresh_token=rotated_refresh_token)

    def revoke_session(self, session_id: str | None) -> None:
        if session_id is None:
            return
        with self.session_factory.begin() as session:
            session_row = session.get(AuthSessionRow, session_id)
            if session_row is None:
                return
            session_row.session_status = "revoked"
            session_row.updated_at = self._utcnow_naive()

    def authorize_access_token(
        self,
        *,
        required_role: str,
        authorization: str | None,
        organization_id: str | None,
    ) -> AuthAccessContext:
        payload = self.token_codec.parse(
            extract_bearer_token(
                authorization,
                error_type=self.error_type,
            )
        )
        token_org_id = str(payload.get("organization_id") or "").strip()
        requested_org_id = (organization_id or "").strip()
        if requested_org_id and requested_org_id != token_org_id:
            raise self.error_type(
                code="AUTH_CONTEXT_INVALID",
                message="Organization context does not match the access token.",
                status_code=400,
            )
        normalized_required_role = self._normalize_role(required_role)
        session_id = str(payload.get("session_id") or "")
        user_id = str(payload.get("user_id") or "")
        with self.session_factory.begin() as session:
            session_row = session.get(AuthSessionRow, session_id)
            if session_row is None:
                raise self.error_type(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Unknown access session.",
                    status_code=401,
                )
            if session_row.session_status == "revoked":
                raise self.error_type(
                    code="AUTH_SESSION_REVOKED",
                    message="Access session has been revoked.",
                    status_code=401,
                )
            now = self._utcnow_naive()
            if session_row.expires_at <= now:
                session_row.session_status = "expired"
                session_row.updated_at = now
                raise self.error_type(
                    code="AUTH_REFRESH_EXPIRED",
                    message="Refresh session has expired.",
                    status_code=401,
                )
            user_row = session.get(AppUserRow, user_id)
            if user_row is None or user_row.status != "active":
                raise self.error_type(
                    code="AUTH_USER_DISABLED",
                    message="User is not active.",
                    status_code=403,
                )
            membership_row = self._load_membership(
                session,
                user_id=user_id,
                organization_id=token_org_id,
            )
            if membership_row is None:
                raise self.error_type(
                    code="AUTH_ORGANIZATION_NOT_ACCESSIBLE",
                    message="User is not an active organization member.",
                    status_code=403,
                )
            normalized_role = require_role(
                required_role=normalized_required_role,
                actual_role=membership_row.role,
                role_priority=self.role_priority,
                role_aliases=self.role_aliases,
                error_type=self.error_type,
            )
        return AuthAccessContext(
            organization_id=token_org_id,
            user_id=user_id,
            role=normalized_role,
            session_id=session_id,
        )

    def get_current_user(self, access_context: AuthAccessContext) -> CurrentUserResponse:
        with self.session_factory() as session:
            user_row = session.get(AppUserRow, access_context.user_id)
            organization_row = session.get(OrganizationRow, access_context.organization_id)
            if user_row is None or organization_row is None:
                raise self.error_type(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Current principal could not be loaded.",
                    status_code=401,
                )
            memberships = self._list_memberships(session, user_id=user_row.user_id)
            return CurrentUserResponse(
                user=SessionUser(
                    id=user_row.user_id,
                    email=user_row.email,
                    display_name=user_row.display_name,
                ),
                active_organization=SessionOrganization(
                    id=organization_row.organization_id,
                    name=organization_row.name,
                    slug=organization_row.slug,
                ),
                memberships=memberships,
            )

    def _build_session_response(
        self,
        *,
        user_row: AppUserRow,
        organization_row: OrganizationRow,
        memberships: list[SessionMembership],
        session_id: str,
        role: str,
    ) -> SessionResponse:
        access_token, expires_at = self.token_codec.mint(
            session_id=session_id,
            user_id=user_row.user_id,
            organization_id=organization_row.organization_id,
            role=self._normalize_role(role),
        )
        return SessionResponse(
            user=SessionUser(
                id=user_row.user_id,
                email=user_row.email,
                display_name=user_row.display_name,
            ),
            active_organization=SessionOrganization(
                id=organization_row.organization_id,
                name=organization_row.name,
                slug=organization_row.slug,
            ),
            memberships=memberships,
            access_token=access_token,
            expires_at=expires_at,
        )

    @staticmethod
    def _load_membership(
        session: Session,
        *,
        user_id: str,
        organization_id: str,
    ) -> OrganizationMembershipRow | None:
        return session.scalars(
            select(OrganizationMembershipRow)
            .where(OrganizationMembershipRow.user_id == user_id)
            .where(OrganizationMembershipRow.organization_id == organization_id)
            .where(OrganizationMembershipRow.status == "active")
            .limit(1)
        ).first()

    @staticmethod
    def _list_memberships(session: Session, *, user_id: str) -> list[SessionMembership]:
        rows = session.scalars(
            select(OrganizationMembershipRow)
            .where(OrganizationMembershipRow.user_id == user_id)
            .order_by(OrganizationMembershipRow.organization_id.asc())
        ).all()
        return [
            SessionMembership(
                organization_id=row.organization_id,
                role=row.role,
                status=row.status,
            )
            for row in rows
        ]

    @staticmethod
    def _hash_refresh_token(refresh_token: str) -> str:
        return hash_token_sha256(refresh_token)

    @staticmethod
    def _hash_password(password: str) -> str:
        return hash_password_pbkdf2(password)

    @staticmethod
    def _verify_password(password: str, password_hash: str | None) -> bool:
        return verify_password_pbkdf2(password, password_hash)

    def _normalize_role(self, role: str) -> str:
        return normalize_role(
            role,
            role_priority=self.role_priority,
            role_aliases=self.role_aliases,
            error_type=self.error_type,
        )

    @staticmethod
    def _utcnow_naive() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
