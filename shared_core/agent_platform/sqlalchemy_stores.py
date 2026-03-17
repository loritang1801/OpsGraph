from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .checkpoints import ReplayRecord, ReplayStore, WorkflowCheckpoint
from .dispatcher import OutboxStore, StoredOutboxEvent
from .events import OutboxEvent
from .errors import RegistryLookupError
from .persistence import WorkflowStateRecord, WorkflowStateStore


class Base(DeclarativeBase):
    pass


class WorkflowStateRow(Base):
    __tablename__ = "workflow_state"

    workflow_run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    workflow_type: Mapped[str] = mapped_column(String(100))
    checkpoint_seq: Mapped[int] = mapped_column(Integer)
    state_payload: Mapped[dict[str, Any]] = mapped_column("state", JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class WorkflowCheckpointRow(Base):
    __tablename__ = "workflow_checkpoint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_type: Mapped[str] = mapped_column(String(100))
    checkpoint_seq: Mapped[int] = mapped_column(Integer, index=True)
    node_name: Mapped[str] = mapped_column(String(100))
    state_before: Mapped[str] = mapped_column(String(100))
    state_after: Mapped[str] = mapped_column(String(100))
    state_patch: Mapped[dict[str, Any]] = mapped_column(JSON)
    warning_codes: Mapped[list[str]] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ReplayRecordRow(Base):
    __tablename__ = "workflow_replay_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_type: Mapped[str] = mapped_column(String(100))
    checkpoint_seq: Mapped[int] = mapped_column(Integer, index=True)
    bundle_id: Mapped[str] = mapped_column(String(150))
    bundle_version: Mapped[str] = mapped_column(String(100))
    model_profile_id: Mapped[str] = mapped_column(String(150))
    response_schema_ref: Mapped[str] = mapped_column(String(150))
    tool_manifest_names: Mapped[list[str]] = mapped_column(JSON)
    input_variable_names: Mapped[list[str]] = mapped_column(JSON)
    output_summary: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class OutboxEventRow(Base):
    __tablename__ = "outbox_event"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(150))
    workflow_run_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_type: Mapped[str] = mapped_column(String(100))
    node_name: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)


def create_runtime_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)


class SqlAlchemyWorkflowStateStore(WorkflowStateStore):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, record: WorkflowStateRecord) -> None:
        with self.session_factory.begin() as session:
            row = session.get(WorkflowStateRow, record.workflow_run_id)
            if row is None:
                row = WorkflowStateRow(
                    workflow_run_id=record.workflow_run_id,
                    workflow_type=record.workflow_type,
                    checkpoint_seq=record.checkpoint_seq,
                    state_payload=record.state,
                    updated_at=record.updated_at,
                )
                session.add(row)
            else:
                row.workflow_type = record.workflow_type
                row.checkpoint_seq = record.checkpoint_seq
                row.state_payload = record.state
                row.updated_at = record.updated_at

    def load(self, workflow_run_id: str) -> WorkflowStateRecord:
        with self.session_factory() as session:
            row = session.get(WorkflowStateRow, workflow_run_id)
            if row is None:
                raise RegistryLookupError(f"Unknown workflow state record: {workflow_run_id}")
            return WorkflowStateRecord(
                workflow_run_id=row.workflow_run_id,
                workflow_type=row.workflow_type,
                checkpoint_seq=row.checkpoint_seq,
                state=row.state_payload,
                updated_at=row.updated_at,
            )


class SqlAlchemyCheckpointStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        with self.session_factory.begin() as session:
            session.add(
                WorkflowCheckpointRow(
                    workflow_run_id=checkpoint.workflow_run_id,
                    workflow_type=checkpoint.workflow_type,
                    checkpoint_seq=checkpoint.checkpoint_seq,
                    node_name=checkpoint.node_name,
                    state_before=checkpoint.state_before,
                    state_after=checkpoint.state_after,
                    state_patch=checkpoint.state_patch,
                    warning_codes=checkpoint.warning_codes,
                    recorded_at=checkpoint.recorded_at,
                )
            )

    def list_for_run(self, workflow_run_id: str) -> list[WorkflowCheckpoint]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(WorkflowCheckpointRow)
                .where(WorkflowCheckpointRow.workflow_run_id == workflow_run_id)
                .order_by(WorkflowCheckpointRow.checkpoint_seq.asc())
            ).all()
            return [
                WorkflowCheckpoint(
                    workflow_run_id=row.workflow_run_id,
                    workflow_type=row.workflow_type,
                    checkpoint_seq=row.checkpoint_seq,
                    node_name=row.node_name,
                    state_before=row.state_before,
                    state_after=row.state_after,
                    state_patch=row.state_patch,
                    warning_codes=row.warning_codes,
                    recorded_at=row.recorded_at,
                )
                for row in rows
            ]


class SqlAlchemyReplayStore(ReplayStore):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, record: ReplayRecord) -> None:
        with self.session_factory.begin() as session:
            session.add(
                ReplayRecordRow(
                    workflow_run_id=record.workflow_run_id,
                    workflow_type=record.workflow_type,
                    checkpoint_seq=record.checkpoint_seq,
                    bundle_id=record.bundle_id,
                    bundle_version=record.bundle_version,
                    model_profile_id=record.model_profile_id,
                    response_schema_ref=record.response_schema_ref,
                    tool_manifest_names=record.tool_manifest_names,
                    input_variable_names=record.input_variable_names,
                    output_summary=record.output_summary,
                    recorded_at=record.recorded_at,
                )
            )

    def list_for_run(self, workflow_run_id: str) -> list[ReplayRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ReplayRecordRow)
                .where(ReplayRecordRow.workflow_run_id == workflow_run_id)
                .order_by(ReplayRecordRow.checkpoint_seq.asc())
            ).all()
            return [
                ReplayRecord(
                    workflow_run_id=row.workflow_run_id,
                    workflow_type=row.workflow_type,
                    checkpoint_seq=row.checkpoint_seq,
                    bundle_id=row.bundle_id,
                    bundle_version=row.bundle_version,
                    model_profile_id=row.model_profile_id,
                    response_schema_ref=row.response_schema_ref,
                    tool_manifest_names=row.tool_manifest_names,
                    input_variable_names=row.input_variable_names,
                    output_summary=row.output_summary,
                    recorded_at=row.recorded_at,
                )
                for row in rows
            ]


class SqlAlchemyOutboxStore(OutboxStore):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def append(self, event: OutboxEvent | dict) -> None:
        normalized = event if isinstance(event, OutboxEvent) else OutboxEvent.model_validate(event)
        with self.session_factory.begin() as session:
            session.merge(
                OutboxEventRow(
                    event_id=normalized.event_id,
                    event_name=normalized.event_name,
                    workflow_run_id=normalized.workflow_run_id,
                    workflow_type=normalized.workflow_type,
                    node_name=normalized.node_name,
                    aggregate_type=normalized.aggregate_type,
                    aggregate_id=normalized.aggregate_id,
                    payload=normalized.payload,
                    emitted_at=normalized.emitted_at,
                    status="pending",
                    dispatched_at=None,
                    failure_message=None,
                )
            )

    def list_pending(self) -> list[StoredOutboxEvent]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(OutboxEventRow)
                .where(OutboxEventRow.status == "pending")
                .order_by(OutboxEventRow.emitted_at.asc())
            ).all()
            return [
                StoredOutboxEvent(
                    event=OutboxEvent(
                        event_id=row.event_id,
                        event_name=row.event_name,
                        workflow_run_id=row.workflow_run_id,
                        workflow_type=row.workflow_type,
                        node_name=row.node_name,
                        aggregate_type=row.aggregate_type,
                        aggregate_id=row.aggregate_id,
                        payload=row.payload,
                        emitted_at=row.emitted_at,
                    ),
                    status=row.status,
                    dispatched_at=row.dispatched_at,
                    failure_message=row.failure_message,
                )
                for row in rows
            ]

    def mark_dispatched(self, event_id: str, dispatched_at: datetime) -> None:
        with self.session_factory.begin() as session:
            row = session.get(OutboxEventRow, event_id)
            if row is None:
                raise RegistryLookupError(f"Unknown outbox event: {event_id}")
            row.status = "dispatched"
            row.dispatched_at = dispatched_at
            row.failure_message = None

    def mark_failed(self, event_id: str, failure_message: str) -> None:
        with self.session_factory.begin() as session:
            row = session.get(OutboxEventRow, event_id)
            if row is None:
                raise RegistryLookupError(f"Unknown outbox event: {event_id}")
            row.status = "failed"
            row.failure_message = failure_message


@dataclass(slots=True)
class SqlAlchemyRuntimeStores:
    engine: Engine
    session_factory: sessionmaker[Session]
    state_store: SqlAlchemyWorkflowStateStore
    checkpoint_store: SqlAlchemyCheckpointStore
    replay_store: SqlAlchemyReplayStore
    outbox_store: SqlAlchemyOutboxStore

    def dispose(self) -> None:
        self.engine.dispose()


def create_sqlalchemy_runtime_stores(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
) -> SqlAlchemyRuntimeStores:
    runtime_engine = engine or create_engine(database_url or "sqlite+pysqlite:///:memory:")
    create_runtime_tables(runtime_engine)
    session_factory = sessionmaker(runtime_engine, expire_on_commit=False)
    return SqlAlchemyRuntimeStores(
        engine=runtime_engine,
        session_factory=session_factory,
        state_store=SqlAlchemyWorkflowStateStore(session_factory),
        checkpoint_store=SqlAlchemyCheckpointStore(session_factory),
        replay_store=SqlAlchemyReplayStore(session_factory),
        outbox_store=SqlAlchemyOutboxStore(session_factory),
    )
