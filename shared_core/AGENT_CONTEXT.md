# SharedAgentCore Agent Context

- Date: 2026-03-16
- Role: shared runtime, schemas, registries, and executable support layer for both products

## What Exists

- `docs/`: shared `ARCHITECTURE`, `DATABASE`, `API`, `WORKFLOW`, `PROMPT_TOOL`
- `agent_platform/shared.py`: registry, schema, tool envelope, workflow state envelope
- `agent_platform/auditflow.py`: AuditFlow workflow state, tool schemas, prompt bundles, tool policies
- `agent_platform/opsgraph.py`: OpsGraph workflow state, tool schemas, prompt bundles, tool policies
- `agent_platform/runtime.py`: prompt assembly and structured output validation
- `agent_platform/tool_executor.py`: tool adapter registration and validated execution
- `agent_platform/node_runtime.py`: specialist node execution, traces, and state patch flow
- `agent_platform/traces.py`: prompt, tool, agent, and node trace models
- `agent_platform/workflow_definitions.py`: concrete demo workflow definitions for both products
- `agent_platform/api_service.py`: workflow registry-backed API service
- `agent_platform/fastapi_adapter.py`: optional FastAPI adapter
- `agent_platform/demo_bootstrap.py`: demo-ready runtime assembly over SQLAlchemy stores
- `agent_platform/persistence.py`: workflow-state persistence adapters
- `agent_platform/dispatcher.py`: outbox store and dispatcher primitives
- `agent_platform/replay.py`: replay fixtures and fixture loader support
- `agent_platform/sqlalchemy_stores.py`: SQLAlchemy-backed state/checkpoint/replay/outbox stores
- `tests/`: runtime catalog, state, prompt assembly, tool execution, workflow, API, and adapter tests

## Current Conventions

- Python-first
- Pydantic v2 models only
- Versioned registries for prompt bundles, tool policies, tools, and model profiles
- Structured output is the only node-to-node protocol
- Shared code must stay self-contained so it can be vendored into product repos
- Product integrations should build on shared `WorkflowExecutionService`, `OutboxDispatcher`, and SQLAlchemy runtime stores instead of bypassing them

## Next Recommended Steps

1. Add richer store implementations for production use beyond SQLite/demo defaults
2. Expose shared approval and artifact helpers that product services can reuse directly
3. Add stronger replay/report utilities for cross-run comparison and artifact exports
4. Keep shared runtime surface stable so vendored copies in product repos do not drift

## Validation

```powershell
python -m unittest discover -s tests -t .
```
