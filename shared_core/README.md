# SharedAgentCore

Shared runtime assets for both `AuditFlow` and `OpsGraph`.

## Layout

- `docs/`: shared architecture, database, API, workflow, and prompt/tool contracts
- `agent_platform/`: Python package for shared registries, schemas, and runtime services
- `agent_platform/product_ci.py`: shared GitHub Actions workflow template plus local CI runner used by product repos
- `tests/`: shared unit tests
- `scripts/`: helper scripts for vendoring this directory into a product repo

## Intended Use

This directory is the single source of truth for shared assets in the local workspace.

When `AuditFlow` and `OpsGraph` become separate GitHub repositories, vendoring should copy this whole directory into each repo as `shared_core/` or an equivalent path. That keeps each repo self-contained while preserving one local source directory during design and prototyping.

## Workspace Sync

From the multi-repo workspace root such as `D:\project`, sync the current shared core into both product repos with:

```powershell
powershell -ExecutionPolicy Bypass -File .\SharedAgentCore\scripts\sync_workspace_repos.ps1
```

To sync only one product repo:

```powershell
powershell -ExecutionPolicy Bypass -File .\SharedAgentCore\scripts\sync_workspace_repos.ps1 -RepoNames AuditFlow
```

When a product repo includes `scripts/render_ci_workflow.py`, the sync step also re-renders that repo's generated GitHub Actions workflow after vendoring `shared_core/`.

## Local Validation

From this directory:

```powershell
python -m unittest discover -s tests -t .
```

Product repos that consume the shared CI template should also expose:

- `python .\scripts\render_ci_workflow.py --check` to verify the committed workflow matches the shared template
- `python .\scripts\run_ci_checks.py` to run the same validation bundle used by the generated GitHub Actions workflow
