# SharedAgentCore

Shared runtime assets for both `AuditFlow` and `OpsGraph`.

## Layout

- `docs/`: shared architecture, database, API, workflow, and prompt/tool contracts
- `agent_platform/`: Python package for shared registries, schemas, and runtime services
- `tests/`: shared unit tests
- `scripts/`: helper scripts for vendoring this directory into a product repo

## Intended Use

This directory is the single source of truth for shared assets in the local workspace.

When `AuditFlow` and `OpsGraph` become separate GitHub repositories, vendoring should copy this whole directory into each repo as `shared_core/` or an equivalent path. That keeps each repo self-contained while preserving one local source directory during design and prototyping.

## Local Validation

From this directory:

```powershell
python -m unittest discover -s tests -t .
```
