from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .errors import RegistryConsistencyError, RegistryLookupError

ModelT = TypeVar("ModelT")


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_langgraph_state(self) -> dict[str, Any]:
        return self.model_dump(mode="python")


class PromptPartSpec(SchemaModel):
    name: Literal[
        "system_identity",
        "developer_constraints",
        "runtime_context",
        "domain_context",
        "memory_context",
        "trigger_payload",
        "tool_manifest",
        "output_contract",
    ]
    description: str
    required_variables: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class PromptVariableContract(SchemaModel):
    name: str
    required: bool = True
    source: Literal[
        "workflow_state",
        "database",
        "retrieval",
        "memory",
        "trigger_payload",
        "computed",
    ]
    transform: list[str] = Field(default_factory=list)
    max_tokens: int | None = None
    sensitivity: Literal["public", "internal", "restricted"] = "internal"


class PromptBundle(SchemaModel):
    bundle_id: str
    bundle_version: str
    workflow_type: Literal["auditflow_cycle", "opsgraph_incident"]
    agent_name: str
    prompt_parts: list[PromptPartSpec]
    variable_contract: list[PromptVariableContract]
    response_schema_ref: str
    model_profile_id: str
    tool_policy_id: str
    tool_policy_version: str
    citation_policy_id: str
    citation_policy_version: str
    context_budget_profile: str
    status: Literal["active", "shadow", "deprecated"] = "active"


class ModelProfile(SchemaModel):
    model_profile_id: str
    profile_kind: Literal[
        "classification",
        "extraction",
        "reasoning",
        "generation",
        "summarization",
    ]
    capabilities: list[str]
    max_output_tokens: int
    supports_tools: bool
    fallback_profile_id: str | None = None
    timeout_ms: int


class CitationPolicy(SchemaModel):
    citation_policy_id: str
    citation_policy_version: str
    requires_citations: bool
    allowed_kinds: list[str]
    visibility_guard: bool = True
    stale_ref_rejected: bool = True


class ToolPolicyEntry(SchemaModel):
    tool_name: str
    tool_version: str
    access_mode: Literal["read_only", "write", "approval_required"]


class ToolPolicy(SchemaModel):
    tool_policy_id: str
    tool_policy_version: str
    agent_name: str
    allowed_tools: list[ToolPolicyEntry]
    max_tool_calls_per_turn: int
    allow_parallel_calls: bool = False
    degraded_mode_behavior: Literal["fail_closed", "continue_partial", "human_gate"]


class ToolDefinition(SchemaModel):
    tool_name: str
    tool_version: str
    category: Literal["artifact", "search", "lookup", "comms", "approval", "connector"]
    access_mode: Literal["read_only", "write", "approval_required"]
    input_schema_ref: str
    output_schema_ref: str
    adapter_type: str
    idempotency_scope: Literal["none", "request", "subject", "publish"]
    default_timeout_ms: int
    auth_context_source: str


class CitationRef(SchemaModel):
    kind: str
    id: str
    locator: dict[str, Any] | None = None


class AuthorizationContext(SchemaModel):
    organization_id: str
    workspace_id: str
    connection_id: str | None = None


class ToolCallEnvelope(SchemaModel):
    tool_call_id: str
    tool_name: str
    tool_version: str
    workflow_run_id: str
    subject_type: str
    subject_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    authorization_context: AuthorizationContext


class ToolResultProvenance(SchemaModel):
    adapter_type: str
    connection_id: str | None = None
    fetched_at: datetime
    source_locator: str


class RawPayloadRef(SchemaModel):
    artifact_id: str
    kind: str


class ToolResultEnvelope(SchemaModel):
    status: Literal["success", "partial", "failed"]
    normalized_payload: dict[str, Any]
    provenance: ToolResultProvenance
    raw_ref: RawPayloadRef | None = None
    warnings: list[str] = Field(default_factory=list)


class SharedAgentOutputEnvelope(SchemaModel):
    status: Literal["success", "partial", "failed"]
    summary: str
    structured_output: dict[str, Any]
    citations: list[CitationRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_human_input: bool = False


class PendingInputGate(SchemaModel):
    gate_type: Literal["domain_input"] = "domain_input"
    reason_code: str
    resume_api: str
    resume_condition: str


class PendingApprovalGate(SchemaModel):
    gate_type: Literal["approval_task"] = "approval_task"
    approval_task_ids: list[str]
    resume_policy: str
    rejection_policy: str


class WorkflowArtifactRef(SchemaModel):
    artifact_id: str
    artifact_type: str
    role: str


class SharedWorkflowStateEnvelope(SchemaModel):
    workflow_run_id: str
    organization_id: str
    workspace_id: str
    workflow_type: str
    subject_type: str
    subject_id: str
    trigger_type: Literal["api_command", "webhook", "system_replay", "system_retry"]
    current_state: str
    status: str = "pending"
    run_config_version: str
    attempt_count: int = 0
    checkpoint_seq: int = 0
    pending_input_gate: PendingInputGate | None = None
    pending_approval_gate: PendingApprovalGate | None = None
    artifact_refs: list[WorkflowArtifactRef] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    error_context: dict[str, Any] | None = None
    last_transition_at: datetime


class VersionedRegistry(Generic[ModelT]):
    def __init__(self, item_label: str) -> None:
        self._item_label = item_label
        self._items: dict[tuple[str, str], ModelT] = {}

    def register(self, name: str, version: str, item: ModelT) -> None:
        key = (name, version)
        if key in self._items:
            raise ValueError(f"Duplicate {self._item_label} registration: {name}@{version}")
        self._items[key] = item

    def get(self, name: str, version: str) -> ModelT:
        key = (name, version)
        if key not in self._items:
            raise RegistryLookupError(f"Unknown {self._item_label}: {name}@{version}")
        return self._items[key]

    def values(self) -> tuple[ModelT, ...]:
        return tuple(self._items.values())

    def items(self) -> tuple[tuple[tuple[str, str], ModelT], ...]:
        return tuple(self._items.items())


class SchemaRegistry:
    def __init__(self) -> None:
        self._items: dict[str, type[BaseModel]] = {}

    def register(self, schema_ref: str, model: type[BaseModel]) -> None:
        if schema_ref in self._items:
            raise ValueError(f"Duplicate schema registration: {schema_ref}")
        self._items[schema_ref] = model

    def get(self, schema_ref: str) -> type[BaseModel]:
        if schema_ref not in self._items:
            raise RegistryLookupError(f"Unknown schema ref: {schema_ref}")
        return self._items[schema_ref]

    def keys(self) -> tuple[str, ...]:
        return tuple(self._items.keys())


@dataclass(slots=True)
class RuntimeCatalog:
    prompt_bundles: VersionedRegistry[PromptBundle]
    model_profiles: VersionedRegistry[ModelProfile]
    citation_policies: VersionedRegistry[CitationPolicy]
    tool_policies: VersionedRegistry[ToolPolicy]
    tools: VersionedRegistry[ToolDefinition]
    schemas: SchemaRegistry

    def validate(self) -> None:
        for bundle in self.prompt_bundles.values():
            self.schemas.get(bundle.response_schema_ref)
            self.model_profiles.get(bundle.model_profile_id, "v1")
            self.citation_policies.get(bundle.citation_policy_id, bundle.citation_policy_version)
            policy = self.tool_policies.get(bundle.tool_policy_id, bundle.tool_policy_version)
            if policy.agent_name != bundle.agent_name:
                raise RegistryConsistencyError(
                    f"Tool policy {policy.tool_policy_id}@{policy.tool_policy_version} "
                    f"does not match agent {bundle.agent_name}"
                )
            for tool_entry in policy.allowed_tools:
                tool = self.tools.get(tool_entry.tool_name, tool_entry.tool_version)
                if tool.access_mode != tool_entry.access_mode:
                    raise RegistryConsistencyError(
                        f"Tool access mode mismatch for {tool.tool_name}@{tool.tool_version}"
                    )
                self.schemas.get(tool.input_schema_ref)
                self.schemas.get(tool.output_schema_ref)


def build_shared_catalog() -> RuntimeCatalog:
    prompt_bundles = VersionedRegistry[PromptBundle]("prompt bundle")
    model_profiles = VersionedRegistry[ModelProfile]("model profile")
    citation_policies = VersionedRegistry[CitationPolicy]("citation policy")
    tool_policies = VersionedRegistry[ToolPolicy]("tool policy")
    tools = VersionedRegistry[ToolDefinition]("tool")
    schemas = SchemaRegistry()

    model_profiles.register(
        "classification.standard",
        "v1",
        ModelProfile(
            model_profile_id="classification.standard",
            profile_kind="classification",
            capabilities=["structured_output", "fast_routing"],
            max_output_tokens=700,
            supports_tools=True,
            timeout_ms=5_000,
        ),
    )
    model_profiles.register(
        "extraction.standard",
        "v1",
        ModelProfile(
            model_profile_id="extraction.standard",
            profile_kind="extraction",
            capabilities=["structured_output", "document_parsing"],
            max_output_tokens=1_200,
            supports_tools=True,
            timeout_ms=10_000,
        ),
    )
    model_profiles.register(
        "reasoning.standard",
        "v1",
        ModelProfile(
            model_profile_id="reasoning.standard",
            profile_kind="reasoning",
            capabilities=["structured_output", "tool_use", "long_context"],
            max_output_tokens=2_000,
            supports_tools=True,
            timeout_ms=20_000,
        ),
    )
    model_profiles.register(
        "generation.grounded",
        "v1",
        ModelProfile(
            model_profile_id="generation.grounded",
            profile_kind="generation",
            capabilities=["structured_output", "grounded_generation", "long_context"],
            max_output_tokens=2_500,
            supports_tools=True,
            timeout_ms=20_000,
        ),
    )
    model_profiles.register(
        "summarization.compact",
        "v1",
        ModelProfile(
            model_profile_id="summarization.compact",
            profile_kind="summarization",
            capabilities=["structured_output", "compression"],
            max_output_tokens=900,
            supports_tools=True,
            timeout_ms=8_000,
        ),
    )

    citation_policies.register(
        "none",
        "v1",
        CitationPolicy(
            citation_policy_id="none",
            citation_policy_version="v1",
            requires_citations=False,
            allowed_kinds=[],
        ),
    )
    citation_policies.register(
        "evidence.required",
        "v1",
        CitationPolicy(
            citation_policy_id="evidence.required",
            citation_policy_version="v1",
            requires_citations=True,
            allowed_kinds=["artifact", "evidence_chunk"],
        ),
    )
    citation_policies.register(
        "facts.required",
        "v1",
        CitationPolicy(
            citation_policy_id="facts.required",
            citation_policy_version="v1",
            requires_citations=True,
            allowed_kinds=["incident_fact", "timeline_event", "deployment"],
        ),
    )

    return RuntimeCatalog(
        prompt_bundles=prompt_bundles,
        model_profiles=model_profiles,
        citation_policies=citation_policies,
        tool_policies=tool_policies,
        tools=tools,
        schemas=schemas,
    )
