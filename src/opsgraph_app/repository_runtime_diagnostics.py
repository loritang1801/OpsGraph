from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select


def list_replay_admin_audit_logs(
    repository,
    workspace_id: str,
    *,
    action_type: str | None = None,
    actor_user_id: str | None = None,
    request_id: str | None = None,
):
    from .repository import ReplayAdminAuditLogRow

    with repository.session_factory() as session:
        stmt = select(ReplayAdminAuditLogRow).where(ReplayAdminAuditLogRow.workspace_id == workspace_id)
        if action_type is not None:
            stmt = stmt.where(ReplayAdminAuditLogRow.action_type == action_type)
        if actor_user_id is not None:
            stmt = stmt.where(ReplayAdminAuditLogRow.actor_user_id == actor_user_id)
        if request_id is not None:
            stmt = stmt.where(ReplayAdminAuditLogRow.request_id == request_id)
        rows = session.scalars(stmt.order_by(ReplayAdminAuditLogRow.created_at.desc())).all()
        return [repository._to_replay_admin_audit_log(row) for row in rows]


def list_remote_provider_smoke_runs(
    repository,
    *,
    limit: int = 10,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    provider: str | None = None,
):
    from .repository import RemoteProviderSmokeRunRow

    with repository.session_factory() as session:
        stmt = select(RemoteProviderSmokeRunRow)
        if actor_user_id is not None:
            stmt = stmt.where(RemoteProviderSmokeRunRow.actor_user_id == actor_user_id)
        if request_id is not None:
            stmt = stmt.where(RemoteProviderSmokeRunRow.request_id == request_id)
        rows = session.scalars(
            stmt.order_by(RemoteProviderSmokeRunRow.created_at.desc())
        ).all()
        records = [repository._to_remote_provider_smoke_run(row) for row in rows]
        if provider is not None:
            records = [
                record
                for record in records
                if provider in record.response.providers
                or any(result.provider == provider for result in record.response.results)
            ]
        return records[:limit]


def record_replay_admin_audit_log(
    repository,
    *,
    workspace_id: str,
    action_type: str,
    subject_type: str | None,
    subject_id: str | None,
    actor_context: dict[str, object] | None = None,
    idempotency_key: str | None = None,
    request_payload: dict[str, object] | None = None,
    result_payload: dict[str, object] | None = None,
):
    from .repository import ReplayAdminAuditLogRow

    with repository.session_factory.begin() as session:
        repository._append_replay_admin_audit_log(
            session,
            workspace_id=workspace_id,
            action_type=action_type,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_context=actor_context,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            result_payload=result_payload,
        )
        row = session.scalars(
            select(ReplayAdminAuditLogRow)
            .where(ReplayAdminAuditLogRow.workspace_id == workspace_id)
            .order_by(ReplayAdminAuditLogRow.created_at.desc())
        ).first()
        assert row is not None
        return repository._to_replay_admin_audit_log(row)


def record_remote_provider_smoke_run(
    repository,
    *,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    actor_context: dict[str, object] | None = None,
):
    from .repository import RemoteProviderSmokeRunRow

    created_at = repository._utcnow_naive()
    diagnostic_run_id = f"runtime-smoke-{uuid4().hex[:8]}"
    actor_context = dict(actor_context or {})
    with repository.session_factory.begin() as session:
        session.add(
            RemoteProviderSmokeRunRow(
                diagnostic_run_id=diagnostic_run_id,
                actor_type=str(actor_context.get("actor_type") or "system"),
                actor_user_id=(
                    str(actor_context["actor_user_id"])
                    if actor_context.get("actor_user_id") is not None
                    else None
                ),
                actor_role=(
                    str(actor_context["actor_role"])
                    if actor_context.get("actor_role") is not None
                    else None
                ),
                session_id=(
                    str(actor_context["session_id"])
                    if actor_context.get("session_id") is not None
                    else None
                ),
                request_id=(
                    str(actor_context["request_id"])
                    if actor_context.get("request_id") is not None
                    else None
                ),
                request_payload=dict(request_payload or {}),
                response_payload=dict(response_payload or {}),
                created_at=created_at,
            )
        )
        row = session.get(RemoteProviderSmokeRunRow, diagnostic_run_id)
        assert row is not None
        return repository._to_remote_provider_smoke_run(row)
