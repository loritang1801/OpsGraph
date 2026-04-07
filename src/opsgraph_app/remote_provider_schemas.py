from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .shared_runtime import load_shared_agent_platform

JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_VERSION = "2026-03-27.1"


class _CanonicalContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalDeploymentLookupRequest(_CanonicalContractModel):
    service_id: str
    incident_id: str | None = None
    limit: int = 10


class CanonicalServiceRegistryLookupRequest(_CanonicalContractModel):
    service_id: str | None = None
    search_query: str | None = None
    limit: int = 5


class CanonicalRunbookSearchRequest(_CanonicalContractModel):
    service_id: str
    query: str
    limit: int = 5


class CanonicalChangeContextRequest(_CanonicalContractModel):
    service_id: str
    incident_id: str | None = None
    limit: int = 3


class CanonicalChangeRecord(_CanonicalContractModel):
    change_id: str
    ticket_ref: str
    summary: str
    status: str
    changed_at: str


class CanonicalChangeContextResponse(_CanonicalContractModel):
    changes: list[CanonicalChangeRecord]


class CanonicalCommsPublishRequest(_CanonicalContractModel):
    incident_id: str
    draft_id: str
    channel_type: str
    title: str
    body_markdown: str
    fact_set_version: int


class CanonicalCommsPublishResponse(_CanonicalContractModel):
    published_message_ref: str | None = None
    delivery_state: Literal["accepted", "published", "failed"]
    delivery_confirmed: bool = False
    provider_delivery_status: str | None = None
    delivery_error: dict[str, Any] | None = None


def remote_provider_schema_output_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "schemas" / "remote_provider_contracts"


def _opsgraph_schema_module():
    shared_platform = load_shared_agent_platform()
    return import_module(f"{shared_platform.__name__}.opsgraph")


def build_remote_provider_schema_models() -> dict[str, type[BaseModel]]:
    opsgraph_module = _opsgraph_schema_module()
    return {
        "deployment_lookup_request.schema.json": CanonicalDeploymentLookupRequest,
        "deployment_lookup_response.schema.json": opsgraph_module.DeploymentLookupResult,
        "service_registry_request.schema.json": CanonicalServiceRegistryLookupRequest,
        "service_registry_response.schema.json": opsgraph_module.ServiceRegistryLookupResult,
        "runbook_search_request.schema.json": CanonicalRunbookSearchRequest,
        "runbook_search_response.schema.json": opsgraph_module.RunbookSearchResult,
        "change_context_request.schema.json": CanonicalChangeContextRequest,
        "change_context_response.schema.json": CanonicalChangeContextResponse,
        "comms_publish_request.schema.json": CanonicalCommsPublishRequest,
        "comms_publish_response.schema.json": CanonicalCommsPublishResponse,
    }


def _schema_document(model: type[BaseModel]) -> dict[str, Any]:
    document = model.model_json_schema()
    return {
        "$schema": JSON_SCHEMA_DRAFT,
        "x-opsgraph-schema-version": SCHEMA_VERSION,
        **document,
    }


def build_remote_provider_schema_documents() -> dict[str, dict[str, Any]]:
    return {
        filename: _schema_document(model)
        for filename, model in build_remote_provider_schema_models().items()
    }


def write_remote_provider_schema_documents(output_dir: Path | None = None) -> list[Path]:
    target_dir = output_dir or remote_provider_schema_output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []
    for filename, document in build_remote_provider_schema_documents().items():
        output_path = target_dir / filename
        output_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=True, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        written_paths.append(output_path)
    return written_paths
