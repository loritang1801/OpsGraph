from __future__ import annotations

from .auditflow import AuditCycleWorkflowState, register_auditflow
from .opsgraph import IncidentWorkflowState, register_opsgraph
from .shared import RuntimeCatalog, build_shared_catalog


def build_default_runtime_catalog() -> RuntimeCatalog:
    catalog = build_shared_catalog()
    register_auditflow(catalog)
    register_opsgraph(catalog)
    catalog.validate()
    return catalog


__all__ = [
    "AuditCycleWorkflowState",
    "IncidentWorkflowState",
    "build_default_runtime_catalog",
]
