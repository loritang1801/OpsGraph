# OpsGraph

OpsGraph is a Python/FastAPI product repo for incident response workflows, replay operations, runtime diagnostics, and remote-provider smoke checks. Detailed design and API documentation live in [docs/PROJECT.md](docs/PROJECT.md).

## Local Run

```powershell
cd D:\project\OpsGraph
python -m pip install --upgrade pip
python -m pip install -e .[api]
Copy-Item .env.example .env

.\start-local.ps1
python .\scripts\run_demo_workflow.py
python .\scripts\run_remote_provider_smoke.py --include-write --allow-write
python .\scripts\run_replay_worker.py --seed-run --supervise --iterations 2 --max-idle-polls 1
python .\scripts\run_ci_checks.py
```

Install `python -m pip install -e .[api,ai]` if you want the optional OpenAI path locally. Local scripts default to [`.local/opsgraph.db`](D:\project\OpsGraph\.local\opsgraph.db); override that with `OPSGRAPH_DATABASE_URL` or `--database-url`. The repo defaults to vendored [`shared_core`](D:\project\OpsGraph\shared_core); set `OPSGRAPH_SHARED_CORE_SOURCE=workspace` only when you intentionally want the sibling `SharedAgentCore` copy.
