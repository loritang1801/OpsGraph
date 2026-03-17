from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .api_models import (
    ApprovalTaskSummary,
    AlertIngestResponse,
    CommsDraftSummary,
    CommsPublishCommand,
    CommsPublishResponse,
    FactCreateCommand,
    FactMutationResponse,
    FactRetractCommand,
    FactSummary,
    HypothesisDecisionCommand,
    HypothesisDecisionResponse,
    HypothesisSummary,
    IncidentSummary,
    IncidentWorkspaceResponse,
    PostmortemSummary,
    ReplayCaseDetail,
    ReplayCaseSummary,
    ReplayBaselineSummary,
    ReplayEvaluationSummary,
    ReplayNodeDiffSummary,
    ReplayNodeSummary,
    RecommendationDecisionCommand,
    RecommendationDecisionResponse,
    RecommendationSummary,
    ReplayRunCommand,
    ReplayStatusCommand,
    ReplayRunSummary,
    ResolveIncidentCommand,
    CloseIncidentCommand,
    SignalSummary,
    SeverityOverrideCommand,
    TimelineEventSummary,
)


class OpsGraphRepository(Protocol):
    def list_incidents(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        severity: str | None = None,
        service_id: str | None = None,
    ) -> list[IncidentSummary]: ...

    def get_incident_workspace(self, incident_id: str) -> IncidentWorkspaceResponse: ...

    def list_hypotheses(self, incident_id: str) -> list[HypothesisSummary]: ...

    def list_recommendations(self, incident_id: str) -> list[RecommendationSummary]: ...

    def list_comms(
        self,
        incident_id: str,
        *,
        channel: str | None = None,
        status: str | None = None,
    ) -> list[CommsDraftSummary]: ...

    def list_approval_tasks(self, incident_id: str) -> list[ApprovalTaskSummary]: ...

    def get_approval_task(self, approval_task_id: str) -> ApprovalTaskSummary: ...

    def ingest_alert(
        self,
        *,
        ops_workspace_id: str,
        correlation_key: str,
        summary: str,
        observed_at: datetime,
        source: str,
    ) -> AlertIngestResponse: ...

    def add_fact(self, incident_id: str, command: FactCreateCommand) -> FactMutationResponse: ...

    def retract_fact(self, incident_id: str, fact_id: str, command: FactRetractCommand) -> FactMutationResponse: ...

    def override_severity(self, incident_id: str, command: SeverityOverrideCommand) -> IncidentSummary: ...

    def decide_hypothesis(
        self,
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand,
    ) -> HypothesisDecisionResponse: ...

    def decide_recommendation(
        self,
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand,
    ) -> RecommendationDecisionResponse: ...

    def publish_comms(
        self,
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand,
    ) -> CommsPublishResponse: ...

    def resolve_incident(self, incident_id: str, command: ResolveIncidentCommand) -> IncidentSummary: ...

    def close_incident(self, incident_id: str, command: CloseIncidentCommand) -> IncidentSummary: ...

    def get_postmortem(self, incident_id: str) -> PostmortemSummary: ...

    def start_replay_run(self, command: ReplayRunCommand) -> ReplayRunSummary: ...

    def list_replays(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
        status: str | None = None,
    ) -> list[ReplayRunSummary]: ...

    def list_replay_cases(self, workspace_id: str, incident_id: str | None = None) -> list[ReplayCaseSummary]: ...

    def get_replay_case(self, replay_case_id: str) -> ReplayCaseDetail: ...

    def update_replay_status(self, replay_run_id: str, command: ReplayStatusCommand) -> ReplayRunSummary: ...

    def get_replay_run(self, replay_run_id: str) -> ReplayRunSummary: ...

    def get_replay_case_input_snapshot(self, replay_case_id: str) -> dict[str, object]: ...

    def mark_replay_execution(
        self,
        replay_run_id: str,
        *,
        status: str,
        workflow_run_id: str | None = None,
        current_state: str | None = None,
        error_message: str | None = None,
    ) -> ReplayRunSummary: ...

    def get_incident_execution_seed(self, incident_id: str) -> dict[str, object]: ...

    def record_replay_baseline(
        self,
        *,
        incident_id: str,
        workflow_run_id: str,
        model_bundle_version: str,
        workflow_type: str,
        final_state: str,
        checkpoint_seq: int,
        node_summaries: list[ReplayNodeSummary],
    ) -> ReplayBaselineSummary: ...

    def list_replay_baselines(self, workspace_id: str, incident_id: str | None = None) -> list[ReplayBaselineSummary]: ...

    def get_replay_baseline(self, baseline_id: str) -> ReplayBaselineSummary: ...

    def record_replay_evaluation(
        self,
        *,
        baseline_id: str,
        replay_run_id: str,
        incident_id: str,
        status: str,
        score: float,
        mismatches: list[str],
        baseline_final_state: str | None = None,
        replay_final_state: str | None = None,
        baseline_checkpoint_seq: int | None = None,
        replay_checkpoint_seq: int | None = None,
        node_diffs: list[ReplayNodeDiffSummary] | None = None,
        report_artifact_path: str | None = None,
    ) -> ReplayEvaluationSummary: ...

    def list_replay_evaluations(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_run_id: str | None = None,
        replay_case_id: str | None = None,
    ) -> list[ReplayEvaluationSummary]: ...

    def attach_replay_evaluation_artifact(
        self,
        report_id: str,
        *,
        report_artifact_path: str,
    ) -> ReplayEvaluationSummary: ...

    def record_incident_response_result(self, incident_id: str, workflow_run_id: str, checkpoint_seq: int) -> None: ...

    def record_retrospective_result(self, incident_id: str, workflow_run_id: str, checkpoint_seq: int) -> None: ...

    def load_idempotency_response(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None: ...

    def store_idempotency_response(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: dict[str, object],
    ) -> None: ...


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "opsgraph_incident"

    incident_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ops_workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    incident_key: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(20))
    incident_status: Mapped[str] = mapped_column(String(50))
    service_name: Mapped[str] = mapped_column(String(100))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    current_fact_set_version: Mapped[int] = mapped_column(Integer, default=1)
    latest_workflow_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class IncidentFactRow(Base):
    __tablename__ = "opsgraph_incident_fact"

    fact_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    fact_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))
    statement: Mapped[str] = mapped_column(Text)
    fact_set_version: Mapped[int] = mapped_column(Integer)
    source_refs: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class HypothesisRow(Base):
    __tablename__ = "opsgraph_hypothesis"

    hypothesis_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(50))
    rank: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class RecommendationRow(Base):
    __tablename__ = "opsgraph_recommendation"

    recommendation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    hypothesis_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    risk_level: Mapped[str] = mapped_column(String(20))
    approval_required: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(50))
    approval_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instructions_markdown: Mapped[str] = mapped_column(Text, default="")
    source_refs: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ApprovalTaskRow(Base):
    __tablename__ = "opsgraph_approval_task"

    approval_task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class CommsDraftRow(Base):
    __tablename__ = "opsgraph_comms_draft"

    draft_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    channel: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50))
    fact_set_version: Mapped[int] = mapped_column(Integer)
    approval_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_message_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class TimelineEventRow(Base):
    __tablename__ = "opsgraph_timeline_event"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class SignalIndexRow(Base):
    __tablename__ = "opsgraph_signal_index"

    correlation_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)


class SignalRow(Base):
    __tablename__ = "opsgraph_signal"

    signal_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    dedupe_key: Mapped[str] = mapped_column(String(255), index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ArtifactBlobRow(Base):
    __tablename__ = "opsgraph_artifact_blob"

    artifact_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(80))
    content_text: Mapped[str] = mapped_column(Text)
    metadata_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class IdempotencyKeyRow(Base):
    __tablename__ = "opsgraph_idempotency_key"

    record_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    request_hash: Mapped[str] = mapped_column(String(128))
    response_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class PostmortemRow(Base):
    __tablename__ = "opsgraph_postmortem"

    postmortem_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True, unique=True)
    status: Mapped[str] = mapped_column(String(50))
    fact_set_version: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replay_case_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ReplayCaseRow(Base):
    __tablename__ = "opsgraph_replay_case"

    replay_case_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ops_workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_type: Mapped[str] = mapped_column(String(100))
    subject_type: Mapped[str] = mapped_column(String(100))
    subject_id: Mapped[str] = mapped_column(String(255), index=True)
    case_name: Mapped[str] = mapped_column(String(255))
    input_snapshot_payload: Mapped[dict] = mapped_column(JSON)
    expected_output_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_workflow_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ReplayRunRow(Base):
    __tablename__ = "opsgraph_replay_run"

    replay_run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ops_workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    replay_case_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50))
    model_bundle_version: Mapped[str] = mapped_column(String(100))
    workflow_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ReplayBaselineRow(Base):
    __tablename__ = "opsgraph_replay_baseline"

    baseline_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ops_workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(255), unique=True)
    model_bundle_version: Mapped[str] = mapped_column(String(100))
    workflow_type: Mapped[str] = mapped_column(String(100))
    final_state: Mapped[str] = mapped_column(String(100))
    checkpoint_seq: Mapped[int] = mapped_column(Integer)
    node_summaries: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class ReplayEvaluationRow(Base):
    __tablename__ = "opsgraph_replay_evaluation"

    report_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ops_workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    baseline_id: Mapped[str] = mapped_column(String(255), index=True)
    replay_run_id: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(30))
    score: Mapped[float] = mapped_column(Float)
    mismatches: Mapped[list[str]] = mapped_column(JSON)
    baseline_final_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    replay_final_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    baseline_checkpoint_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replay_checkpoint_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_diffs: Mapped[list[dict]] = mapped_column(JSON)
    report_artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


def create_opsgraph_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)


class SqlAlchemyOpsGraphRepository:
    def __init__(self, session_factory: sessionmaker[Session], engine: Engine) -> None:
        self.session_factory = session_factory
        self.engine = engine
        create_opsgraph_tables(engine)
        self.seed_if_empty()

    @classmethod
    def from_runtime_stores(cls, runtime_stores) -> "SqlAlchemyOpsGraphRepository":
        return cls(runtime_stores.session_factory, runtime_stores.engine)

    def seed_if_empty(self) -> None:
        with self.session_factory.begin() as session:
            existing = session.scalar(select(IncidentRow.incident_id).limit(1))
            if existing is not None:
                return
            created_at = datetime(2026, 3, 16, 9, 0, tzinfo=UTC)
            session.add(
                IncidentRow(
                    incident_id="incident-1",
                    ops_workspace_id="ops-ws-1",
                    incident_key="INC-2026-0001",
                    title="Checkout API elevated 5xx rate",
                    severity="sev2",
                    incident_status="investigating",
                    service_name="checkout-api",
                    opened_at=created_at,
                    acknowledged_at=datetime(2026, 3, 16, 9, 1, tzinfo=UTC),
                    current_fact_set_version=1,
                    latest_workflow_run_id=None,
                    updated_at=created_at,
                )
            )
            session.add(
                IncidentFactRow(
                    fact_id="fact-1",
                    incident_id="incident-1",
                    fact_type="impact",
                    status="confirmed",
                    statement="Rollback restored checkout availability.",
                    fact_set_version=1,
                    source_refs=[{"kind": "deployment", "id": "deploy-123"}],
                    created_at=created_at,
                )
            )
            session.add(
                HypothesisRow(
                    hypothesis_id="hypothesis-1",
                    incident_id="incident-1",
                    status="proposed",
                    rank=1,
                    confidence=0.81,
                    title="Latest checkout deployment exhausted database connections",
                    rationale="Deployment timing aligns with the start of saturation metrics.",
                    evidence_refs=[{"kind": "deployment", "id": "deploy-123"}],
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.add(
                RecommendationRow(
                    recommendation_id="recommendation-1",
                    incident_id="incident-1",
                    hypothesis_id="hypothesis-1",
                    title="Rollback latest checkout deployment",
                    risk_level="medium",
                    approval_required=True,
                    status="pending_approval",
                    approval_task_id="approval-task-1",
                    instructions_markdown="Rollback deploy-123 and monitor connection pool recovery.",
                    source_refs=[{"kind": "deployment", "id": "deploy-123"}],
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.add(
                ApprovalTaskRow(
                    approval_task_id="approval-task-1",
                    incident_id="incident-1",
                    recommendation_id="recommendation-1",
                    status="pending",
                    comment=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.add(
                CommsDraftRow(
                    draft_id="draft-1",
                    incident_id="incident-1",
                    channel="internal_slack",
                    title="Checkout API incident update",
                    status="draft",
                    fact_set_version=1,
                    approval_task_id=None,
                    published_message_ref=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.add(
                SignalRow(
                    signal_id="signal-1",
                    incident_id="incident-1",
                    source="grafana",
                    status="firing",
                    title="HighErrorRate",
                    dedupe_key="checkout-api:high-error-rate",
                    fired_at=created_at,
                )
            )
            session.add(
                TimelineEventRow(
                    event_id="timeline-1",
                    incident_id="incident-1",
                    kind="signal_ingested",
                    summary="Grafana alert detected elevated 5xx errors on checkout-api.",
                    created_at=created_at,
                )
            )
            session.add(SignalIndexRow(correlation_key="checkout-api:high-error-rate", incident_id="incident-1"))

    @staticmethod
    def _to_incident(row: IncidentRow) -> IncidentSummary:
        return IncidentSummary(
            incident_id=row.incident_id,
            incident_key=row.incident_key,
            title=row.title,
            severity=row.severity,
            incident_status=row.incident_status,
            service_name=row.service_name,
            opened_at=row.opened_at,
            acknowledged_at=row.acknowledged_at,
            current_fact_set_version=row.current_fact_set_version,
            latest_workflow_run_id=row.latest_workflow_run_id,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_fact(row: IncidentFactRow) -> FactSummary:
        return FactSummary(
            fact_id=row.fact_id,
            fact_type=row.fact_type,
            status=row.status,
            statement=row.statement,
            fact_set_version=row.fact_set_version,
            source_refs=row.source_refs,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_signal(row: SignalRow) -> SignalSummary:
        return SignalSummary(
            signal_id=row.signal_id,
            source=row.source,
            status=row.status,
            title=row.title,
            dedupe_key=row.dedupe_key,
            fired_at=row.fired_at,
        )

    @staticmethod
    def _to_hypothesis(row: HypothesisRow) -> HypothesisSummary:
        return HypothesisSummary(
            hypothesis_id=row.hypothesis_id,
            status=row.status,
            rank=row.rank,
            confidence=row.confidence,
            title=row.title,
            rationale=row.rationale,
            evidence_refs=row.evidence_refs,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_recommendation(row: RecommendationRow) -> RecommendationSummary:
        return RecommendationSummary(
            recommendation_id=row.recommendation_id,
            title=row.title,
            risk_level=row.risk_level,
            approval_required=row.approval_required,
            status=row.status,
            hypothesis_id=row.hypothesis_id,
            approval_task_id=row.approval_task_id,
        )

    @staticmethod
    def _to_comms(row: CommsDraftRow) -> CommsDraftSummary:
        return CommsDraftSummary(
            draft_id=row.draft_id,
            channel=row.channel,
            title=row.title,
            status=row.status,
            fact_set_version=row.fact_set_version,
            approval_task_id=row.approval_task_id,
            published_message_ref=row.published_message_ref,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_timeline(row: TimelineEventRow) -> TimelineEventSummary:
        return TimelineEventSummary(
            event_id=row.event_id,
            kind=row.kind,
            summary=row.summary,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_approval_task(row: ApprovalTaskRow) -> ApprovalTaskSummary:
        return ApprovalTaskSummary(
            approval_task_id=row.approval_task_id,
            incident_id=row.incident_id,
            recommendation_id=row.recommendation_id,
            status=row.status,
            comment=row.comment,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_postmortem(row: PostmortemRow) -> PostmortemSummary:
        return PostmortemSummary(
            postmortem_id=row.postmortem_id,
            incident_id=row.incident_id,
            status=row.status,
            fact_set_version=row.fact_set_version,
            artifact_id=row.artifact_id,
            replay_case_id=row.replay_case_id,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_replay(row: ReplayRunRow) -> ReplayRunSummary:
        return ReplayRunSummary(
            replay_run_id=row.replay_run_id,
            incident_id=row.incident_id,
            status=row.status,
            model_bundle_version=row.model_bundle_version,
            replay_case_id=row.replay_case_id,
            workflow_run_id=row.workflow_run_id,
            current_state=row.current_state,
            error_message=row.error_message,
            created_at=row.created_at,
        )

    @staticmethod
    def _validate_replay_status_transition(current_status: str, next_status: str) -> bool:
        if current_status == next_status:
            return False
        allowed_transitions = {
            "queued": {"running", "completed", "failed"},
            "running": {"completed", "failed"},
            "completed": set(),
            "failed": set(),
        }
        return next_status in allowed_transitions.get(current_status, set())

    @staticmethod
    def _to_replay_case_summary(row: ReplayCaseRow) -> ReplayCaseSummary:
        return ReplayCaseSummary(
            replay_case_id=row.replay_case_id,
            incident_id=row.incident_id,
            workflow_type=row.workflow_type,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            case_name=row.case_name,
            source_workflow_run_id=row.source_workflow_run_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _to_replay_case_detail(cls, row: ReplayCaseRow) -> ReplayCaseDetail:
        summary = cls._to_replay_case_summary(row)
        return ReplayCaseDetail(
            **summary.model_dump(),
            input_snapshot=dict(row.input_snapshot_payload),
            expected_output=(
                dict(row.expected_output_payload)
                if isinstance(row.expected_output_payload, dict)
                else row.expected_output_payload
            ),
        )

    @staticmethod
    def _to_replay_baseline(row: ReplayBaselineRow) -> ReplayBaselineSummary:
        return ReplayBaselineSummary(
            baseline_id=row.baseline_id,
            incident_id=row.incident_id,
            workflow_run_id=row.workflow_run_id,
            model_bundle_version=row.model_bundle_version,
            workflow_type=row.workflow_type,
            final_state=row.final_state,
            checkpoint_seq=row.checkpoint_seq,
            node_summaries=[ReplayNodeSummary.model_validate(item) for item in row.node_summaries],
            created_at=row.created_at,
        )

    @staticmethod
    def _to_replay_evaluation(row: ReplayEvaluationRow) -> ReplayEvaluationSummary:
        node_diffs = [ReplayNodeDiffSummary.model_validate(item) for item in row.node_diffs]
        metrics = SqlAlchemyOpsGraphRepository._replay_evaluation_metrics(
            node_diffs=node_diffs,
            report_artifact_path=row.report_artifact_path,
        )
        return ReplayEvaluationSummary(
            report_id=row.report_id,
            baseline_id=row.baseline_id,
            replay_run_id=row.replay_run_id,
            incident_id=row.incident_id,
            status=row.status,  # type: ignore[arg-type]
            score=row.score,
            mismatch_count=len(row.mismatches),
            matched_node_count=metrics["matched_node_count"],
            mismatched_node_count=metrics["mismatched_node_count"],
            bundle_mismatch_count=metrics["bundle_mismatch_count"],
            summary_mismatch_count=metrics["summary_mismatch_count"],
            latency_regression_count=metrics["latency_regression_count"],
            max_latency_delta_ms=metrics["max_latency_delta_ms"],
            mismatches=row.mismatches,
            baseline_final_state=row.baseline_final_state,
            replay_final_state=row.replay_final_state,
            baseline_checkpoint_seq=row.baseline_checkpoint_seq,
            replay_checkpoint_seq=row.replay_checkpoint_seq,
            node_diffs=node_diffs,
            report_artifact_path=row.report_artifact_path,
            markdown_report_path=metrics["markdown_report_path"],
            created_at=row.created_at,
        )

    def list_incidents(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        severity: str | None = None,
        service_id: str | None = None,
    ) -> list[IncidentSummary]:
        with self.session_factory() as session:
            stmt = (
                select(IncidentRow)
                .where(IncidentRow.ops_workspace_id == workspace_id)
                .order_by(IncidentRow.opened_at.desc())
            )
            if status is not None:
                stmt = stmt.where(IncidentRow.incident_status == status)
            if severity is not None:
                stmt = stmt.where(IncidentRow.severity == severity)
            if service_id is not None:
                stmt = stmt.where(IncidentRow.service_name == service_id)
            rows = session.scalars(stmt).all()
            return [self._to_incident(row) for row in rows]

    def get_incident_workspace(self, incident_id: str) -> IncidentWorkspaceResponse:
        with self.session_factory() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            signal_rows = session.scalars(
                select(SignalRow)
                .where(SignalRow.incident_id == incident_id)
                .order_by(SignalRow.fired_at.desc())
            ).all()
            fact_rows = session.scalars(
                select(IncidentFactRow)
                .where(IncidentFactRow.incident_id == incident_id)
                .where(IncidentFactRow.status != "retracted")
                .order_by(IncidentFactRow.created_at.asc())
            ).all()
            hypothesis_rows = session.scalars(
                select(HypothesisRow)
                .where(HypothesisRow.incident_id == incident_id)
                .order_by(HypothesisRow.rank.asc(), HypothesisRow.updated_at.desc())
            ).all()
            recommendation_rows = session.scalars(
                select(RecommendationRow)
                .where(RecommendationRow.incident_id == incident_id)
                .order_by(RecommendationRow.recommendation_id.asc())
            ).all()
            approval_rows = session.scalars(
                select(ApprovalTaskRow)
                .where(ApprovalTaskRow.incident_id == incident_id)
                .order_by(ApprovalTaskRow.created_at.asc())
            ).all()
            comms_rows = session.scalars(
                select(CommsDraftRow)
                .where(CommsDraftRow.incident_id == incident_id)
                .order_by(CommsDraftRow.draft_id.asc())
            ).all()
            timeline_rows = session.scalars(
                select(TimelineEventRow)
                .where(TimelineEventRow.incident_id == incident_id)
                .order_by(TimelineEventRow.created_at.asc())
            ).all()
            return IncidentWorkspaceResponse(
                incident=self._to_incident(incident_row),
                signals=[self._to_signal(row) for row in signal_rows],
                confirmed_facts=[self._to_fact(row) for row in fact_rows],
                hypotheses=[self._to_hypothesis(row) for row in hypothesis_rows],
                recommendations=[self._to_recommendation(row) for row in recommendation_rows],
                approval_tasks=[self._to_approval_task(row) for row in approval_rows],
                comms_drafts=[self._to_comms(row) for row in comms_rows],
                timeline=[self._to_timeline(row) for row in timeline_rows],
            )

    def list_hypotheses(self, incident_id: str) -> list[HypothesisSummary]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(HypothesisRow)
                .where(HypothesisRow.incident_id == incident_id)
                .order_by(HypothesisRow.rank.asc(), HypothesisRow.updated_at.desc())
            ).all()
            return [self._to_hypothesis(row) for row in rows]

    def list_recommendations(self, incident_id: str) -> list[RecommendationSummary]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(RecommendationRow)
                .where(RecommendationRow.incident_id == incident_id)
                .order_by(RecommendationRow.recommendation_id.asc())
            ).all()
            return [self._to_recommendation(row) for row in rows]

    def list_comms(
        self,
        incident_id: str,
        *,
        channel: str | None = None,
        status: str | None = None,
    ) -> list[CommsDraftSummary]:
        with self.session_factory() as session:
            stmt = select(CommsDraftRow).where(CommsDraftRow.incident_id == incident_id)
            if channel is not None:
                stmt = stmt.where(CommsDraftRow.channel == channel)
            if status is not None:
                stmt = stmt.where(CommsDraftRow.status == status)
            rows = session.scalars(stmt.order_by(CommsDraftRow.draft_id.asc())).all()
            return [self._to_comms(row) for row in rows]

    def list_approval_tasks(self, incident_id: str) -> list[ApprovalTaskSummary]:
        with self.session_factory() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            rows = session.scalars(
                select(ApprovalTaskRow)
                .where(ApprovalTaskRow.incident_id == incident_id)
                .order_by(ApprovalTaskRow.created_at.asc())
            ).all()
            return [self._to_approval_task(row) for row in rows]

    def get_approval_task(self, approval_task_id: str) -> ApprovalTaskSummary:
        with self.session_factory() as session:
            row = session.get(ApprovalTaskRow, approval_task_id)
            if row is None:
                raise KeyError(approval_task_id)
            return self._to_approval_task(row)

    def ingest_alert(
        self,
        *,
        ops_workspace_id: str,
        correlation_key: str,
        summary: str,
        observed_at: datetime,
        source: str,
    ) -> AlertIngestResponse:
        signal_id = f"signal-{uuid4().hex[:8]}"
        workflow_run_id = f"opsgraph-alert-{signal_id}"
        with self.session_factory.begin() as session:
            signal_row = session.get(SignalIndexRow, correlation_key)
            incident_created = False
            if signal_row is None:
                incident_created = True
                incident_id = f"incident-{uuid4().hex[:8]}"
                session.add(
                    IncidentRow(
                        incident_id=incident_id,
                        ops_workspace_id=ops_workspace_id,
                        incident_key=f"INC-{observed_at.year}-{self._next_incident_number(session):04d}",
                        title=summary,
                        severity="sev2",
                        incident_status="investigating",
                        service_name=correlation_key.split(":")[0],
                        opened_at=observed_at,
                        acknowledged_at=observed_at,
                        current_fact_set_version=1,
                        latest_workflow_run_id=workflow_run_id,
                        updated_at=self._normalize_timestamp(observed_at),
                    )
                )
                session.add(SignalIndexRow(correlation_key=correlation_key, incident_id=incident_id))
            else:
                incident_id = signal_row.incident_id
                incident_row = session.get(IncidentRow, incident_id)
                if incident_row is not None:
                    incident_row.updated_at = self._normalize_timestamp(observed_at)  # type: ignore[assignment]
                    incident_row.latest_workflow_run_id = workflow_run_id
                    if incident_row.acknowledged_at is None:
                        incident_row.acknowledged_at = observed_at
            session.add(
                SignalRow(
                    signal_id=signal_id,
                    incident_id=incident_id,
                    source=source,
                    status="firing",
                    title=summary,
                    dedupe_key=correlation_key,
                    fired_at=self._normalize_timestamp(observed_at) or self._utcnow_naive(),
                )
            )
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="signal_ingested",
                    summary=f"{source} alert ingested: {summary}",
                    created_at=observed_at,
                )
            )
        return AlertIngestResponse(
            signal_id=signal_id,
            incident_id=incident_id,
            incident_created=incident_created,
            accepted_signals=1,
            workflow_run_id=workflow_run_id,
        )

    def add_fact(self, incident_id: str, command: FactCreateCommand) -> FactMutationResponse:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            if incident_row.current_fact_set_version != command.expected_fact_set_version:
                raise ValueError("FACT_VERSION_CONFLICT")
            incident_row.current_fact_set_version += 1
            incident_row.updated_at = self._utcnow_naive()
            fact_id = f"fact-{uuid4().hex[:8]}"
            session.add(
                IncidentFactRow(
                    fact_id=fact_id,
                    incident_id=incident_id,
                    fact_type=command.fact_type,
                    status="confirmed",
                    statement=command.statement,
                    fact_set_version=incident_row.current_fact_set_version,
                    source_refs=command.source_refs,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="fact_confirmed",
                    summary=command.statement,
                    created_at=datetime.now(UTC),
                )
            )
            return FactMutationResponse(
                fact_id=fact_id,
                status="confirmed",
                current_fact_set_version=incident_row.current_fact_set_version,
            )

    def retract_fact(self, incident_id: str, fact_id: str, command: FactRetractCommand) -> FactMutationResponse:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            fact_row = session.get(IncidentFactRow, fact_id)
            if incident_row is None or fact_row is None or fact_row.incident_id != incident_id:
                raise KeyError(fact_id)
            if incident_row.current_fact_set_version != command.expected_fact_set_version:
                raise ValueError("FACT_VERSION_CONFLICT")
            incident_row.current_fact_set_version += 1
            incident_row.updated_at = self._utcnow_naive()
            fact_row.status = "retracted"
            fact_row.fact_set_version = incident_row.current_fact_set_version
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="fact_retracted",
                    summary=command.reason,
                    created_at=datetime.now(UTC),
                )
            )
            return FactMutationResponse(
                fact_id=fact_id,
                status="retracted",
                current_fact_set_version=incident_row.current_fact_set_version,
            )

    def override_severity(self, incident_id: str, command: SeverityOverrideCommand) -> IncidentSummary:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            if command.expected_updated_at is not None and not self._timestamps_match(
                incident_row.updated_at,
                command.expected_updated_at,
            ):
                raise ValueError("CONFLICT_STALE_RESOURCE")
            incident_row.severity = command.severity
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="severity_changed",
                    summary=command.reason,
                    created_at=datetime.now(UTC),
                )
            )
            return self._to_incident(incident_row)

    def decide_hypothesis(
        self,
        incident_id: str,
        hypothesis_id: str,
        command: HypothesisDecisionCommand,
    ) -> HypothesisDecisionResponse:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            hypothesis_row = session.get(HypothesisRow, hypothesis_id)
            if incident_row is None or hypothesis_row is None or hypothesis_row.incident_id != incident_id:
                raise KeyError(hypothesis_id)
            if command.expected_updated_at is not None and not self._timestamps_match(
                hypothesis_row.updated_at,
                command.expected_updated_at,
            ):
                raise ValueError("CONFLICT_STALE_RESOURCE")
            if hypothesis_row.status in {"accepted", "rejected"}:
                raise ValueError("HYPOTHESIS_STATUS_CONFLICT")
            hypothesis_row.status = "accepted" if command.decision == "accept" else "rejected"
            hypothesis_row.updated_at = self._utcnow_naive()
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="hypothesis_resolved",
                    summary=f"{hypothesis_row.title}: {hypothesis_row.status}",
                    created_at=datetime.now(UTC),
                )
            )
            return HypothesisDecisionResponse(
                hypothesis_id=hypothesis_id,
                status=hypothesis_row.status,
            )

    def decide_recommendation(
        self,
        incident_id: str,
        recommendation_id: str,
        command: RecommendationDecisionCommand,
    ) -> RecommendationDecisionResponse:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            recommendation_row = session.get(RecommendationRow, recommendation_id)
            if (
                incident_row is None
                or recommendation_row is None
                or recommendation_row.incident_id != incident_id
            ):
                raise KeyError(recommendation_id)
            approval_row = None
            if recommendation_row.approval_task_id is not None:
                approval_row = session.get(ApprovalTaskRow, recommendation_row.approval_task_id)
                if approval_row is not None and (
                    approval_row.incident_id != incident_id
                    or approval_row.recommendation_id != recommendation_id
                ):
                    raise ValueError("APPROVAL_REQUIRED")
            if command.expected_updated_at is not None and not self._timestamps_match(
                recommendation_row.updated_at,
                command.expected_updated_at,
            ):
                raise ValueError("CONFLICT_STALE_RESOURCE")
            if recommendation_row.status in {"executed", "rejected"}:
                raise ValueError("RECOMMENDATION_STATUS_CONFLICT")
            if (
                command.approval_task_id is not None
                and recommendation_row.approval_task_id is not None
                and command.approval_task_id != recommendation_row.approval_task_id
            ):
                raise ValueError("APPROVAL_REQUIRED")
            if recommendation_row.approval_required and command.decision in {"approve", "mark_executed", "reject"}:
                if approval_row is None and not command.approval_task_id:
                    raise ValueError("APPROVAL_REQUIRED")
                if approval_row is None and command.approval_task_id:
                    approval_row = ApprovalTaskRow(
                        approval_task_id=command.approval_task_id,
                        incident_id=incident_id,
                        recommendation_id=recommendation_id,
                        status="pending",
                        comment=None,
                        created_at=self._utcnow_naive(),
                        updated_at=self._utcnow_naive(),
                    )
                    session.add(approval_row)
                    recommendation_row.approval_task_id = command.approval_task_id
                elif approval_row is not None:
                    recommendation_row.approval_task_id = approval_row.approval_task_id
            if command.decision == "approve":
                if recommendation_row.status == "approved":
                    raise ValueError("RECOMMENDATION_STATUS_CONFLICT")
                recommendation_row.status = "approved"
                if approval_row is not None:
                    approval_row.status = "approved"
                    approval_row.comment = command.comment or None
                    approval_row.updated_at = self._utcnow_naive()
            elif command.decision == "reject":
                if recommendation_row.status == "approved":
                    raise ValueError("RECOMMENDATION_STATUS_CONFLICT")
                recommendation_row.status = "rejected"
                if approval_row is not None:
                    approval_row.status = "rejected"
                    approval_row.comment = command.comment or None
                    approval_row.updated_at = self._utcnow_naive()
            else:
                if recommendation_row.approval_required and (approval_row is None or approval_row.status != "approved"):
                    raise ValueError("APPROVAL_REQUIRED")
                if recommendation_row.approval_required and recommendation_row.status != "approved":
                    raise ValueError("RECOMMENDATION_STATUS_CONFLICT")
                recommendation_row.status = "executed"
            recommendation_row.updated_at = self._utcnow_naive()
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="recommendation_updated",
                    summary=f"{recommendation_row.title}: {recommendation_row.status}",
                    created_at=datetime.now(UTC),
                )
            )
            return RecommendationDecisionResponse(
                recommendation_id=recommendation_id,
                status=recommendation_row.status,
            )

    def publish_comms(
        self,
        incident_id: str,
        draft_id: str,
        command: CommsPublishCommand,
    ) -> CommsPublishResponse:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            comms_row = session.get(CommsDraftRow, draft_id)
            if incident_row is None or comms_row is None or comms_row.incident_id != incident_id:
                raise KeyError(draft_id)
            if comms_row.status == "published":
                raise ValueError("COMM_DRAFT_ALREADY_PUBLISHED")
            if incident_row.current_fact_set_version != comms_row.fact_set_version:
                raise ValueError("COMM_DRAFT_STALE_FACT_SET")
            if comms_row.fact_set_version != command.expected_fact_set_version:
                raise ValueError("COMM_DRAFT_STALE_FACT_SET")
            if comms_row.approval_task_id is not None:
                if command.approval_task_id != comms_row.approval_task_id:
                    raise ValueError("APPROVAL_REQUIRED")
                approval_row = session.get(ApprovalTaskRow, comms_row.approval_task_id)
                if (
                    approval_row is None
                    or approval_row.incident_id != incident_id
                    or approval_row.status != "approved"
                ):
                    raise ValueError("APPROVAL_REQUIRED")
            comms_row.status = "published"
            comms_row.published_message_ref = f"{comms_row.channel}-msg-{uuid4().hex[:8]}"
            comms_row.updated_at = self._utcnow_naive()
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="comms_published",
                    summary=f"Published {comms_row.channel} draft {draft_id}.",
                    created_at=datetime.now(UTC),
                )
            )
            return CommsPublishResponse(
                draft_id=draft_id,
                status=comms_row.status,
                published_message_ref=comms_row.published_message_ref,
            )

    def resolve_incident(self, incident_id: str, command: ResolveIncidentCommand) -> IncidentSummary:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            if command.expected_updated_at is not None and not self._timestamps_match(
                incident_row.updated_at,
                command.expected_updated_at,
            ):
                raise ValueError("CONFLICT_STALE_RESOURCE")
            if incident_row.incident_status in {"resolved", "closed"}:
                raise ValueError("INCIDENT_ALREADY_RESOLVED")
            if not command.root_cause_fact_ids:
                raise ValueError("ROOT_CAUSE_FACT_REQUIRED")
            confirmed_root_cause_fact_ids = set(
                session.scalars(
                    select(IncidentFactRow.fact_id)
                    .where(IncidentFactRow.incident_id == incident_id)
                    .where(IncidentFactRow.status == "confirmed")
                    .where(IncidentFactRow.fact_id.in_(tuple(command.root_cause_fact_ids)))
                ).all()
            )
            if len(confirmed_root_cause_fact_ids) != len(set(command.root_cause_fact_ids)):
                raise ValueError("ROOT_CAUSE_FACT_REQUIRED")
            incident_row.incident_status = "resolved"
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="incident_resolved",
                    summary=command.resolution_summary,
                    created_at=datetime.now(UTC),
                )
            )
            return self._to_incident(incident_row)

    def close_incident(self, incident_id: str, command: CloseIncidentCommand) -> IncidentSummary:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            if command.expected_updated_at is not None and not self._timestamps_match(
                incident_row.updated_at,
                command.expected_updated_at,
            ):
                raise ValueError("CONFLICT_STALE_RESOURCE")
            if incident_row.incident_status != "resolved":
                raise ValueError("INCIDENT_NOT_RESOLVED")
            incident_row.incident_status = "closed"
            incident_row.updated_at = self._utcnow_naive()
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="incident_closed",
                    summary=command.close_reason,
                    created_at=datetime.now(UTC),
                )
            )
            return self._to_incident(incident_row)

    def get_postmortem(self, incident_id: str) -> PostmortemSummary:
        with self.session_factory() as session:
            row = session.scalars(
                select(PostmortemRow).where(PostmortemRow.incident_id == incident_id).limit(1)
            ).first()
            if row is None:
                raise KeyError(incident_id)
            return self._to_postmortem(row)

    def start_replay_run(self, command: ReplayRunCommand) -> ReplayRunSummary:
        replay_run_id = f"replay-{uuid4().hex[:8]}"
        created_at = self._utcnow_naive()
        with self.session_factory.begin() as session:
            if command.incident_id is not None:
                incident_row = session.get(IncidentRow, command.incident_id)
                if incident_row is None:
                    raise KeyError(command.incident_id)
                incident_id = incident_row.incident_id
                ops_workspace_id = incident_row.ops_workspace_id
            else:
                replay_case_row = session.get(ReplayCaseRow, command.replay_case_id)
                if replay_case_row is None:
                    raise KeyError(command.replay_case_id)
                incident_id = replay_case_row.incident_id
                ops_workspace_id = replay_case_row.ops_workspace_id
            session.add(
                ReplayRunRow(
                    replay_run_id=replay_run_id,
                    ops_workspace_id=ops_workspace_id,
                    incident_id=incident_id,
                    replay_case_id=command.replay_case_id,
                    status="queued",
                    model_bundle_version=command.model_bundle_version,
                    workflow_run_id=None,
                    current_state=None,
                    error_message=None,
                    created_at=created_at,
                )
            )
        return ReplayRunSummary(
            replay_run_id=replay_run_id,
            incident_id=incident_id,
            status="queued",
            model_bundle_version=command.model_bundle_version,
            replay_case_id=command.replay_case_id,
            created_at=created_at,
        )

    def list_replays(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_case_id: str | None = None,
        status: str | None = None,
    ) -> list[ReplayRunSummary]:
        with self.session_factory() as session:
            stmt = select(ReplayRunRow).where(ReplayRunRow.ops_workspace_id == workspace_id)
            if incident_id is not None:
                stmt = stmt.where(ReplayRunRow.incident_id == incident_id)
            if replay_case_id is not None:
                stmt = stmt.where(ReplayRunRow.replay_case_id == replay_case_id)
            if status is not None:
                stmt = stmt.where(ReplayRunRow.status == status)
            rows = session.scalars(stmt.order_by(ReplayRunRow.created_at.desc())).all()
            return [self._to_replay(row) for row in rows]

    def list_replay_cases(self, workspace_id: str, incident_id: str | None = None) -> list[ReplayCaseSummary]:
        with self.session_factory() as session:
            stmt = select(ReplayCaseRow).where(ReplayCaseRow.ops_workspace_id == workspace_id)
            if incident_id is not None:
                stmt = stmt.where(ReplayCaseRow.incident_id == incident_id)
            rows = session.scalars(stmt.order_by(ReplayCaseRow.updated_at.desc())).all()
            return [self._to_replay_case_summary(row) for row in rows]

    def get_replay_case(self, replay_case_id: str) -> ReplayCaseDetail:
        with self.session_factory() as session:
            row = session.get(ReplayCaseRow, replay_case_id)
            if row is None:
                raise KeyError(replay_case_id)
            return self._to_replay_case_detail(row)

    def get_replay_run(self, replay_run_id: str) -> ReplayRunSummary:
        with self.session_factory() as session:
            row = session.get(ReplayRunRow, replay_run_id)
            if row is None:
                raise KeyError(replay_run_id)
            return self._to_replay(row)

    def get_replay_case_input_snapshot(self, replay_case_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            row = session.get(ReplayCaseRow, replay_case_id)
            if row is None:
                raise KeyError(replay_case_id)
            return dict(row.input_snapshot_payload)

    def record_replay_baseline(
        self,
        *,
        incident_id: str,
        workflow_run_id: str,
        model_bundle_version: str,
        workflow_type: str,
        final_state: str,
        checkpoint_seq: int,
        node_summaries: list[ReplayNodeSummary],
    ) -> ReplayBaselineSummary:
        baseline_id = f"baseline-{uuid4().hex[:8]}"
        created_at = self._utcnow_naive()
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            session.add(
                ReplayBaselineRow(
                    baseline_id=baseline_id,
                    ops_workspace_id=incident_row.ops_workspace_id,
                    incident_id=incident_id,
                    workflow_run_id=workflow_run_id,
                    model_bundle_version=model_bundle_version,
                    workflow_type=workflow_type,
                    final_state=final_state,
                    checkpoint_seq=checkpoint_seq,
                    node_summaries=[item.model_dump(mode="json") for item in node_summaries],
                    created_at=created_at,
                )
            )
        return ReplayBaselineSummary(
            baseline_id=baseline_id,
            incident_id=incident_id,
            workflow_run_id=workflow_run_id,
            model_bundle_version=model_bundle_version,
            workflow_type=workflow_type,
            final_state=final_state,
            checkpoint_seq=checkpoint_seq,
            node_summaries=node_summaries,
            created_at=created_at,
        )

    def list_replay_baselines(self, workspace_id: str, incident_id: str | None = None) -> list[ReplayBaselineSummary]:
        with self.session_factory() as session:
            stmt = select(ReplayBaselineRow).where(ReplayBaselineRow.ops_workspace_id == workspace_id)
            if incident_id is not None:
                stmt = stmt.where(ReplayBaselineRow.incident_id == incident_id)
            rows = session.scalars(stmt.order_by(ReplayBaselineRow.created_at.desc())).all()
            return [self._to_replay_baseline(row) for row in rows]

    def get_replay_baseline(self, baseline_id: str) -> ReplayBaselineSummary:
        with self.session_factory() as session:
            row = session.get(ReplayBaselineRow, baseline_id)
            if row is None:
                raise KeyError(baseline_id)
            return self._to_replay_baseline(row)

    def record_replay_evaluation(
        self,
        *,
        baseline_id: str,
        replay_run_id: str,
        incident_id: str,
        status: str,
        score: float,
        mismatches: list[str],
        baseline_final_state: str | None = None,
        replay_final_state: str | None = None,
        baseline_checkpoint_seq: int | None = None,
        replay_checkpoint_seq: int | None = None,
        node_diffs: list[ReplayNodeDiffSummary] | None = None,
        report_artifact_path: str | None = None,
        ) -> ReplayEvaluationSummary:
        report_id = f"report-{uuid4().hex[:8]}"
        created_at = self._utcnow_naive()
        node_diffs = node_diffs or []
        metrics = self._replay_evaluation_metrics(
            node_diffs=node_diffs,
            report_artifact_path=report_artifact_path,
        )
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            session.add(
                ReplayEvaluationRow(
                    report_id=report_id,
                    ops_workspace_id=incident_row.ops_workspace_id,
                    baseline_id=baseline_id,
                    replay_run_id=replay_run_id,
                    incident_id=incident_id,
                    status=status,
                    score=score,
                    mismatches=mismatches,
                    baseline_final_state=baseline_final_state,
                    replay_final_state=replay_final_state,
                    baseline_checkpoint_seq=baseline_checkpoint_seq,
                    replay_checkpoint_seq=replay_checkpoint_seq,
                    node_diffs=[item.model_dump() for item in node_diffs],
                    report_artifact_path=report_artifact_path,
                    created_at=created_at,
                )
            )
        return ReplayEvaluationSummary(
            report_id=report_id,
            baseline_id=baseline_id,
            replay_run_id=replay_run_id,
            incident_id=incident_id,
            status=status,  # type: ignore[arg-type]
            score=score,
            mismatch_count=len(mismatches),
            matched_node_count=metrics["matched_node_count"],
            mismatched_node_count=metrics["mismatched_node_count"],
            bundle_mismatch_count=metrics["bundle_mismatch_count"],
            summary_mismatch_count=metrics["summary_mismatch_count"],
            latency_regression_count=metrics["latency_regression_count"],
            max_latency_delta_ms=metrics["max_latency_delta_ms"],
            mismatches=mismatches,
            baseline_final_state=baseline_final_state,
            replay_final_state=replay_final_state,
            baseline_checkpoint_seq=baseline_checkpoint_seq,
            replay_checkpoint_seq=replay_checkpoint_seq,
            node_diffs=node_diffs,
            report_artifact_path=report_artifact_path,
            markdown_report_path=metrics["markdown_report_path"],
            created_at=created_at,
        )

    def list_replay_evaluations(
        self,
        workspace_id: str,
        incident_id: str | None = None,
        replay_run_id: str | None = None,
        replay_case_id: str | None = None,
    ) -> list[ReplayEvaluationSummary]:
        with self.session_factory() as session:
            stmt = select(ReplayEvaluationRow).where(ReplayEvaluationRow.ops_workspace_id == workspace_id)
            if incident_id is not None:
                stmt = stmt.where(ReplayEvaluationRow.incident_id == incident_id)
            if replay_run_id is not None:
                stmt = stmt.where(ReplayEvaluationRow.replay_run_id == replay_run_id)
            if replay_case_id is not None:
                matching_run_ids = session.scalars(
                    select(ReplayRunRow.replay_run_id).where(ReplayRunRow.replay_case_id == replay_case_id)
                ).all()
                if not matching_run_ids:
                    return []
                stmt = stmt.where(ReplayEvaluationRow.replay_run_id.in_(matching_run_ids))
            rows = session.scalars(stmt.order_by(ReplayEvaluationRow.created_at.desc())).all()
            return [self._to_replay_evaluation(row) for row in rows]

    def attach_replay_evaluation_artifact(
        self,
        report_id: str,
        *,
        report_artifact_path: str,
    ) -> ReplayEvaluationSummary:
        with self.session_factory.begin() as session:
            row = session.get(ReplayEvaluationRow, report_id)
            if row is None:
                raise KeyError(report_id)
            row.report_artifact_path = report_artifact_path
            return self._to_replay_evaluation(row)

    @staticmethod
    def _replay_evaluation_metrics(
        *,
        node_diffs: list[ReplayNodeDiffSummary],
        report_artifact_path: str | None,
    ) -> dict[str, object]:
        matched_node_count = sum(1 for item in node_diffs if item.matched)
        mismatched_node_count = sum(1 for item in node_diffs if not item.matched)
        bundle_mismatch_count = sum(
            1 for item in node_diffs if any("bundle mismatch" in reason for reason in item.mismatch_reasons)
        )
        summary_mismatch_count = sum(
            1 for item in node_diffs if any("summary mismatch" in reason for reason in item.mismatch_reasons)
        )
        latency_regression_count = sum(
            1 for item in node_diffs if item.latency_delta_ms is not None and item.latency_delta_ms > 0
        )
        latency_deltas = [item.latency_delta_ms for item in node_diffs if item.latency_delta_ms is not None]
        markdown_report_path = (
            str(Path(report_artifact_path).with_suffix(".md"))
            if report_artifact_path is not None
            else None
        )
        return {
            "matched_node_count": matched_node_count,
            "mismatched_node_count": mismatched_node_count,
            "bundle_mismatch_count": bundle_mismatch_count,
            "summary_mismatch_count": summary_mismatch_count,
            "latency_regression_count": latency_regression_count,
            "max_latency_delta_ms": max(latency_deltas) if latency_deltas else None,
            "markdown_report_path": markdown_report_path,
        }

    def update_replay_status(self, replay_run_id: str, command: ReplayStatusCommand) -> ReplayRunSummary:
        with self.session_factory.begin() as session:
            replay_row = session.get(ReplayRunRow, replay_run_id)
            if replay_row is None:
                raise KeyError(replay_run_id)
            if not self._validate_replay_status_transition(replay_row.status, command.status):
                if replay_row.status == command.status:
                    return self._to_replay(replay_row)
                raise ValueError("REPLAY_STATUS_CONFLICT")
            replay_row.status = command.status
            incident_row = session.get(IncidentRow, replay_row.incident_id)
            if incident_row is not None:
                incident_row.updated_at = self._utcnow_naive()
                session.add(
                    TimelineEventRow(
                        event_id=f"timeline-{uuid4().hex[:8]}",
                        incident_id=incident_row.incident_id,
                        kind="replay_status_updated",
                        summary=f"Replay {replay_run_id} -> {command.status}",
                        created_at=datetime.now(UTC),
                    )
                )
            return self._to_replay(replay_row)

    def mark_replay_execution(
        self,
        replay_run_id: str,
        *,
        status: str,
        workflow_run_id: str | None = None,
        current_state: str | None = None,
        error_message: str | None = None,
    ) -> ReplayRunSummary:
        with self.session_factory.begin() as session:
            replay_row = session.get(ReplayRunRow, replay_run_id)
            if replay_row is None:
                raise KeyError(replay_run_id)
            if not self._validate_replay_status_transition(replay_row.status, status):
                if replay_row.status == status:
                    return self._to_replay(replay_row)
                raise ValueError("REPLAY_STATUS_CONFLICT")
            replay_row.status = status
            replay_row.workflow_run_id = workflow_run_id
            replay_row.current_state = current_state
            replay_row.error_message = error_message
            incident_row = session.get(IncidentRow, replay_row.incident_id)
            if incident_row is not None:
                incident_row.updated_at = self._utcnow_naive()
                session.add(
                    TimelineEventRow(
                        event_id=f"timeline-{uuid4().hex[:8]}",
                        incident_id=incident_row.incident_id,
                        kind="replay_status_updated",
                        summary=f"Replay {replay_run_id} -> {status}",
                        created_at=datetime.now(UTC),
                    )
                )
            return self._to_replay(replay_row)

    def get_incident_execution_seed(self, incident_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            return self._build_incident_execution_seed(session, incident_row)

    def record_incident_response_result(self, incident_id: str, workflow_run_id: str, checkpoint_seq: int) -> None:
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            incident_row.incident_status = (
                "responding" if checkpoint_seq < 4 else "resolved_pending_confirmation"
            )
            incident_row.severity = "sev1"
            incident_row.latest_workflow_run_id = workflow_run_id
            incident_row.updated_at = self._utcnow_naive()

            hypothesis_exists = session.scalar(
                select(HypothesisRow.hypothesis_id).where(HypothesisRow.incident_id == incident_id).limit(1)
            )
            if hypothesis_exists is None:
                session.add(
                    HypothesisRow(
                        hypothesis_id="hypothesis-generated-1",
                        incident_id=incident_id,
                        status="proposed",
                        rank=1,
                        confidence=0.73,
                        title="Generated incident hypothesis",
                        rationale="Workflow generated a top-ranked root-cause candidate.",
                        evidence_refs=[],
                        created_at=self._utcnow_naive(),
                        updated_at=self._utcnow_naive(),
                    )
                )

            recommendation_row = session.scalars(
                select(RecommendationRow)
                .where(RecommendationRow.incident_id == incident_id)
                .order_by(RecommendationRow.created_at.asc())
                .limit(1)
            ).first()
            approval_task_id = recommendation_row.approval_task_id if recommendation_row is not None else None
            if recommendation_row is None:
                approval_task_id = f"approval-task-{uuid4().hex[:8]}"
                session.add(
                    RecommendationRow(
                        recommendation_id="recommendation-generated-1",
                        incident_id=incident_id,
                        hypothesis_id="hypothesis-generated-1",
                        title="Scale checkout workers",
                        risk_level="medium",
                        approval_required=True,
                        status="pending_approval",
                        approval_task_id=approval_task_id,
                        instructions_markdown="Scale worker pool and watch queue depth.",
                        source_refs=[],
                        created_at=self._utcnow_naive(),
                        updated_at=self._utcnow_naive(),
                    )
                )
                session.add(
                    ApprovalTaskRow(
                        approval_task_id=approval_task_id,
                        incident_id=incident_id,
                        recommendation_id="recommendation-generated-1",
                        status="pending",
                        comment=None,
                        created_at=self._utcnow_naive(),
                        updated_at=self._utcnow_naive(),
                    )
                )
            comms_exists = session.scalar(
                select(CommsDraftRow.draft_id).where(CommsDraftRow.incident_id == incident_id).limit(1)
            )
            if comms_exists is None:
                session.add(
                    CommsDraftRow(
                        draft_id="draft-generated-1",
                        incident_id=incident_id,
                        channel="internal_slack",
                        title="Generated incident update",
                        status="draft",
                        fact_set_version=incident_row.current_fact_set_version,
                        approval_task_id=approval_task_id,
                        published_message_ref=None,
                        created_at=self._utcnow_naive(),
                        updated_at=self._utcnow_naive(),
                    )
                )

    def record_retrospective_result(self, incident_id: str, workflow_run_id: str, checkpoint_seq: int) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            incident_row = session.get(IncidentRow, incident_id)
            if incident_row is None:
                raise KeyError(incident_id)
            incident_row.incident_status = "closed" if checkpoint_seq > 0 else "resolved"
            incident_row.latest_workflow_run_id = workflow_run_id
            incident_row.updated_at = self._normalize_timestamp(now)  # type: ignore[assignment]
            session.add(
                TimelineEventRow(
                    event_id=f"timeline-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    kind="postmortem_ready",
                    summary="Postmortem draft generated.",
                    created_at=now,
                )
            )
            postmortem_row = session.scalars(
                select(PostmortemRow).where(PostmortemRow.incident_id == incident_id).limit(1)
            ).first()
            if postmortem_row is None:
                replay_case_id = f"replay-case-{uuid4().hex[:8]}"
                postmortem_row = PostmortemRow(
                    postmortem_id=f"postmortem-{uuid4().hex[:8]}",
                    incident_id=incident_id,
                    status="draft",
                    fact_set_version=incident_row.current_fact_set_version,
                    artifact_id=f"artifact-postmortem-{incident_id}",
                    replay_case_id=replay_case_id,
                    updated_at=now,
                )
                session.add(postmortem_row)
            else:
                replay_case_id = postmortem_row.replay_case_id or f"replay-case-{uuid4().hex[:8]}"
                postmortem_row.status = "draft"
                postmortem_row.fact_set_version = incident_row.current_fact_set_version
                postmortem_row.artifact_id = postmortem_row.artifact_id or f"artifact-postmortem-{incident_id}"
                postmortem_row.replay_case_id = replay_case_id
                postmortem_row.updated_at = now
            replay_case_row = session.get(ReplayCaseRow, replay_case_id)
            input_snapshot = self._build_incident_execution_seed(session, incident_row)
            fact_rows = session.scalars(
                select(IncidentFactRow)
                .where(IncidentFactRow.incident_id == incident_id)
                .where(IncidentFactRow.status == "confirmed")
                .order_by(IncidentFactRow.created_at.asc())
            ).all()
            timeline_rows = session.scalars(
                select(TimelineEventRow)
                .where(TimelineEventRow.incident_id == incident_id)
                .order_by(TimelineEventRow.created_at.asc())
            ).all()
            if replay_case_row is None:
                session.add(
                    ReplayCaseRow(
                        replay_case_id=replay_case_id,
                        ops_workspace_id=incident_row.ops_workspace_id,
                        incident_id=incident_id,
                        workflow_type="opsgraph_incident",
                        subject_type="incident",
                        subject_id=incident_id,
                        case_name=f"{incident_row.incident_key} retrospective replay",
                        input_snapshot_payload=input_snapshot,
                        expected_output_payload=None,
                        source_workflow_run_id=workflow_run_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                replay_case_row.input_snapshot_payload = input_snapshot
                replay_case_row.case_name = f"{incident_row.incident_key} retrospective replay"
                replay_case_row.source_workflow_run_id = workflow_run_id
                replay_case_row.updated_at = now
            session.merge(
                ArtifactBlobRow(
                    artifact_id=postmortem_row.artifact_id,
                    artifact_type="opsgraph_postmortem",
                    content_text=json.dumps(
                        {
                            "postmortem_id": postmortem_row.postmortem_id,
                            "incident_id": incident_id,
                            "incident_key": incident_row.incident_key,
                            "status": postmortem_row.status,
                            "fact_set_version": incident_row.current_fact_set_version,
                            "workflow_run_id": workflow_run_id,
                            "replay_case_id": replay_case_id,
                            "facts": [
                                {
                                    "fact_id": row.fact_id,
                                    "fact_type": row.fact_type,
                                    "statement": row.statement,
                                }
                                for row in fact_rows
                            ],
                            "timeline": [
                                {
                                    "event_id": row.event_id,
                                    "kind": row.kind,
                                    "summary": row.summary,
                                }
                                for row in timeline_rows
                            ],
                        },
                        sort_keys=True,
                    ),
                    metadata_payload={
                        "incident_id": incident_id,
                        "postmortem_id": postmortem_row.postmortem_id,
                        "replay_case_id": replay_case_id,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )

    @staticmethod
    def _normalize_timestamp(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @classmethod
    def _utcnow_naive(cls) -> datetime:
        return cls._normalize_timestamp(datetime.now(UTC))  # type: ignore[return-value]

    @classmethod
    def _timestamps_match(cls, stored: datetime, expected: datetime) -> bool:
        return cls._normalize_timestamp(stored) == cls._normalize_timestamp(expected)

    @staticmethod
    def _idempotency_record_id(operation: str, idempotency_key: str) -> str:
        return f"{operation}:{idempotency_key}"

    def load_idempotency_response(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None:
        record_id = self._idempotency_record_id(operation, idempotency_key)
        with self.session_factory() as session:
            row = session.get(IdempotencyKeyRow, record_id)
            if row is None:
                return None
            if row.request_hash != request_hash:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            payload = row.response_payload if isinstance(row.response_payload, dict) else {}
            return dict(payload)

    def store_idempotency_response(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: dict[str, object],
    ) -> None:
        now = self._utcnow_naive()
        record_id = self._idempotency_record_id(operation, idempotency_key)
        with self.session_factory.begin() as session:
            session.merge(
                IdempotencyKeyRow(
                    record_id=record_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response_payload=dict(response_payload),
                    created_at=now,
                    updated_at=now,
                )
            )

    @staticmethod
    def _build_incident_execution_seed(session: Session, incident_row: IncidentRow) -> dict[str, object]:
        signal_rows = session.scalars(
            select(SignalRow)
            .where(SignalRow.incident_id == incident_row.incident_id)
            .order_by(SignalRow.fired_at.asc())
        ).all()
        fact_rows = session.scalars(
            select(IncidentFactRow)
            .where(IncidentFactRow.incident_id == incident_row.incident_id)
            .where(IncidentFactRow.status == "confirmed")
            .order_by(IncidentFactRow.created_at.asc())
        ).all()
        hypothesis_rows = session.scalars(
            select(HypothesisRow)
            .where(HypothesisRow.incident_id == incident_row.incident_id)
            .where(HypothesisRow.status != "rejected")
            .order_by(HypothesisRow.rank.asc())
        ).all()
        comms_rows = session.scalars(
            select(CommsDraftRow)
            .where(CommsDraftRow.incident_id == incident_row.incident_id)
            .order_by(CommsDraftRow.created_at.asc())
        ).all()
        return {
            "incident_id": incident_row.incident_id,
            "ops_workspace_id": incident_row.ops_workspace_id,
            "signal_ids": [row.signal_id for row in signal_rows],
            "signal_summaries": [
                {
                    "signal_id": row.signal_id,
                    "source": row.source,
                    "correlation_key": row.dedupe_key,
                    "summary": row.title,
                    "observed_at": row.fired_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
                }
                for row in signal_rows
            ],
            "current_incident_candidates": [],
            "context_bundle_id": "context-1",
            "current_fact_set_version": incident_row.current_fact_set_version,
            "confirmed_fact_refs": [
                {"kind": "incident_fact", "id": row.fact_id} for row in fact_rows
            ],
            "top_hypothesis_refs": [
                {"kind": "hypothesis", "id": row.hypothesis_id} for row in hypothesis_rows[:3]
            ],
            "target_channels": [row.channel for row in comms_rows] or ["internal_slack"],
            "organization_id": "org-1",
            "workspace_id": "ws-1",
        }

    @staticmethod
    def _next_incident_number(session: Session) -> int:
        incident_ids = session.scalars(select(IncidentRow.incident_id)).all()
        return len(incident_ids) + 1
