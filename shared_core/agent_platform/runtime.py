from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from .errors import OutputValidationError, PromptAssemblyError
from .shared import RuntimeCatalog, SchemaModel, ToolDefinition


class PromptAssemblySources(SchemaModel):
    workflow_state: dict[str, Any] = Field(default_factory=dict)
    database: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    computed: dict[str, Any] = Field(default_factory=dict)


class AssembledPromptPart(SchemaModel):
    name: str
    description: str
    instructions: list[str]
    variables: dict[str, Any]


class ToolManifestEntry(SchemaModel):
    tool_name: str
    tool_version: str
    access_mode: str
    category: str
    input_schema_ref: str
    output_schema_ref: str
    adapter_type: str


class AssembledPrompt(SchemaModel):
    bundle_id: str
    bundle_version: str
    agent_name: str
    workflow_type: str
    model_profile_id: str
    citation_policy_id: str
    response_schema_ref: str
    context_budget_profile: str
    parts: list[AssembledPromptPart]
    resolved_variables: dict[str, Any]
    tool_manifest: list[ToolManifestEntry]


def _apply_transforms(value: Any, transforms: list[str]) -> Any:
    transformed = value
    for transform in transforms:
        if transform == "truncate" and isinstance(transformed, str):
            transformed = transformed[:2_000]
        elif transform == "top_k" and isinstance(transformed, list):
            transformed = transformed[:5]
    return transformed


class PromptAssemblyService:
    def __init__(self, catalog: RuntimeCatalog) -> None:
        self.catalog = catalog

    def assemble(
        self,
        *,
        bundle_id: str,
        bundle_version: str,
        sources: PromptAssemblySources,
    ) -> AssembledPrompt:
        bundle = self.catalog.prompt_bundles.get(bundle_id, bundle_version)
        resolved_variables: dict[str, Any] = {}

        for variable in bundle.variable_contract:
            source_values = getattr(sources, variable.source)
            if variable.name not in source_values:
                if variable.required:
                    raise PromptAssemblyError(
                        f"Missing required variable {variable.name} from {variable.source} "
                        f"for bundle {bundle.bundle_id}@{bundle.bundle_version}"
                    )
                continue
            resolved_variables[variable.name] = _apply_transforms(
                source_values[variable.name], variable.transform
            )

        parts: list[AssembledPromptPart] = []
        for part in bundle.prompt_parts:
            part_vars = {
                name: resolved_variables[name]
                for name in part.required_variables
                if name in resolved_variables
            }
            missing = [name for name in part.required_variables if name not in part_vars]
            if missing:
                raise PromptAssemblyError(
                    f"Prompt part {part.name} missing variables {missing} for "
                    f"bundle {bundle.bundle_id}@{bundle.bundle_version}"
                )
            parts.append(
                AssembledPromptPart(
                    name=part.name,
                    description=part.description,
                    instructions=part.instructions,
                    variables=part_vars,
                )
            )

        policy = self.catalog.tool_policies.get(bundle.tool_policy_id, bundle.tool_policy_version)
        tool_manifest = [self._build_tool_manifest_entry(entry.tool_name, entry.tool_version) for entry in policy.allowed_tools]

        return AssembledPrompt(
            bundle_id=bundle.bundle_id,
            bundle_version=bundle.bundle_version,
            agent_name=bundle.agent_name,
            workflow_type=bundle.workflow_type,
            model_profile_id=bundle.model_profile_id,
            citation_policy_id=bundle.citation_policy_id,
            response_schema_ref=bundle.response_schema_ref,
            context_budget_profile=bundle.context_budget_profile,
            parts=parts,
            resolved_variables=resolved_variables,
            tool_manifest=tool_manifest,
        )

    def validate_output(
        self,
        *,
        bundle_id: str,
        bundle_version: str,
        payload: dict[str, Any],
    ) -> None:
        bundle = self.catalog.prompt_bundles.get(bundle_id, bundle_version)
        schema = self.catalog.schemas.get(bundle.response_schema_ref)
        try:
            schema.model_validate(payload)
        except ValidationError as exc:
            raise OutputValidationError(
                f"Output validation failed for {bundle.bundle_id}@{bundle.bundle_version}"
            ) from exc
        return None

    def _build_tool_manifest_entry(self, tool_name: str, tool_version: str) -> ToolManifestEntry:
        tool: ToolDefinition = self.catalog.tools.get(tool_name, tool_version)
        return ToolManifestEntry(
            tool_name=tool.tool_name,
            tool_version=tool.tool_version,
            access_mode=tool.access_mode,
            category=tool.category,
            input_schema_ref=tool.input_schema_ref,
            output_schema_ref=tool.output_schema_ref,
            adapter_type=tool.adapter_type,
        )
