from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field

from .errors import RegistryLookupError
from .node_runtime import NodeExecutionContext
from .runtime import AssembledPrompt
from .shared import SchemaModel, SharedAgentOutputEnvelope, ToolResultEnvelope
from .traces import AgentInvocationResult, ToolExecutionTrace


class ReplayToolFixture(SchemaModel):
    tool_call_id: str
    tool_name: str
    tool_version: str
    envelope: ToolResultEnvelope


class ReplayFixture(SchemaModel):
    fixture_key: str
    workflow_type: str
    node_name: str
    bundle_id: str
    bundle_version: str
    expected_output: SharedAgentOutputEnvelope
    tool_fixtures: list[ReplayToolFixture] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class ReplayFixtureStore(Protocol):
    def save(self, fixture: ReplayFixture) -> None: ...

    def get(self, fixture_key: str) -> ReplayFixture: ...


class InMemoryReplayFixtureStore:
    def __init__(self) -> None:
        self._fixtures: dict[str, ReplayFixture] = {}

    def save(self, fixture: ReplayFixture) -> None:
        self._fixtures[fixture.fixture_key] = fixture

    def get(self, fixture_key: str) -> ReplayFixture:
        if fixture_key not in self._fixtures:
            raise RegistryLookupError(f"Unknown replay fixture: {fixture_key}")
        return self._fixtures[fixture_key]


class ReplayFixtureLoader:
    def __init__(self, fixture_store: ReplayFixtureStore) -> None:
        self.fixture_store = fixture_store

    @staticmethod
    def make_fixture_key(*, workflow_run_id: str, checkpoint_seq: int, node_name: str) -> str:
        return f"{workflow_run_id}:{checkpoint_seq}:{node_name}"

    def load(self, fixture_key: str) -> ReplayFixture:
        return self.fixture_store.get(fixture_key)

    def build_invoker(self, fixture_key: str) -> "ReplayAgentInvoker":
        fixture = self.load(fixture_key)
        return ReplayAgentInvoker(fixture)


class ReplayAgentInvoker:
    def __init__(self, fixture: ReplayFixture) -> None:
        self.fixture = fixture

    def invoke(
        self,
        *,
        assembled_prompt: AssembledPrompt,
        context: NodeExecutionContext,
    ) -> AgentInvocationResult:
        if assembled_prompt.bundle_id != self.fixture.bundle_id or assembled_prompt.bundle_version != self.fixture.bundle_version:
            raise RegistryLookupError(
                f"Replay fixture {self.fixture.fixture_key} does not match "
                f"{assembled_prompt.bundle_id}@{assembled_prompt.bundle_version}"
            )
        now = datetime.now(UTC)
        return AgentInvocationResult(
            agent_output=self.fixture.expected_output,
            tool_traces=[
                ToolExecutionTrace(
                    tool_call_id=tool_fixture.tool_call_id,
                    tool_name=tool_fixture.tool_name,
                    tool_version=tool_fixture.tool_version,
                    adapter_type=tool_fixture.envelope.provenance.adapter_type,
                    status=tool_fixture.envelope.status,
                    warnings=tool_fixture.envelope.warnings,
                    started_at=now,
                    finished_at=now,
                )
                for tool_fixture in self.fixture.tool_fixtures
            ],
            tool_results=[tool_fixture.envelope for tool_fixture in self.fixture.tool_fixtures],
        )
