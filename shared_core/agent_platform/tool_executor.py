from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .errors import ToolAdapterNotRegisteredError, ToolExecutionError
from .shared import RuntimeCatalog, ToolCallEnvelope, ToolDefinition, ToolResultEnvelope
from .traces import ToolExecutionTrace


class ToolAdapter(Protocol):
    def execute(
        self,
        *,
        tool: ToolDefinition,
        call: ToolCallEnvelope,
        arguments: BaseModel,
    ) -> ToolResultEnvelope | dict[str, Any]: ...


class StaticToolAdapter:
    def __init__(self, result: ToolResultEnvelope | dict[str, Any]) -> None:
        self._result = result

    def execute(
        self,
        *,
        tool: ToolDefinition,
        call: ToolCallEnvelope,
        arguments: BaseModel,
    ) -> ToolResultEnvelope | dict[str, Any]:
        return self._result


class ToolExecutionOutcome(BaseModel):
    envelope: ToolResultEnvelope
    trace: ToolExecutionTrace


class ToolExecutor:
    def __init__(self, catalog: RuntimeCatalog) -> None:
        self.catalog = catalog
        self._adapters: dict[str, ToolAdapter] = {}

    def register_adapter(self, adapter_type: str, adapter: ToolAdapter) -> None:
        self._adapters[adapter_type] = adapter

    def execute(self, call: ToolCallEnvelope) -> ToolExecutionOutcome:
        tool = self.catalog.tools.get(call.tool_name, call.tool_version)
        if tool.adapter_type not in self._adapters:
            raise ToolAdapterNotRegisteredError(
                f"No adapter registered for {tool.adapter_type} ({tool.tool_name}@{tool.tool_version})"
            )

        args_schema = self.catalog.schemas.get(tool.input_schema_ref)
        output_schema = self.catalog.schemas.get(tool.output_schema_ref)
        started_at = datetime.now(UTC)

        try:
            validated_arguments = args_schema.model_validate(call.arguments)
            raw_result = self._adapters[tool.adapter_type].execute(
                tool=tool,
                call=call,
                arguments=validated_arguments,
            )
            envelope = raw_result if isinstance(raw_result, ToolResultEnvelope) else ToolResultEnvelope.model_validate(raw_result)
            if envelope.provenance.adapter_type != tool.adapter_type:
                raise ToolExecutionError(
                    f"Adapter type mismatch for {tool.tool_name}@{tool.tool_version}: "
                    f"{envelope.provenance.adapter_type} != {tool.adapter_type}"
                )
            output_schema.model_validate(envelope.normalized_payload)
        except ValidationError as exc:
            raise ToolExecutionError(
                f"Tool schema validation failed for {tool.tool_name}@{tool.tool_version}"
            ) from exc
        except ToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolExecutionError(
                f"Tool execution failed for {tool.tool_name}@{tool.tool_version}"
            ) from exc

        finished_at = datetime.now(UTC)
        return ToolExecutionOutcome(
            envelope=envelope,
            trace=ToolExecutionTrace(
                tool_call_id=call.tool_call_id,
                tool_name=tool.tool_name,
                tool_version=tool.tool_version,
                adapter_type=tool.adapter_type,
                status=envelope.status,
                warnings=envelope.warnings,
                started_at=started_at,
                finished_at=finished_at,
            ),
        )
