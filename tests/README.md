# OpsGraph Tests

Current OpsGraph tests cover:

- bootstrap and FastAPI factory wiring
- alert intake and incident mutation flows
- approval, comms, resolve/close, and postmortem behavior
- replay case, replay run, baseline, execution, and evaluation coverage
- replay worker polling and supervisor heartbeat coverage
- route helper and error mapping behavior
- remote provider contracts backed by canonical request/response fixtures under `tests/fixtures/remote_provider_contracts/`

Run from the repo root with:

```powershell
python -m unittest discover -s tests -t .
```

Regenerate the committed remote-provider JSON Schema files with:

```powershell
python .\scripts\generate_remote_provider_schemas.py
```

Verify the committed GitHub Actions workflow still matches the shared CI template with:

```powershell
python .\scripts\render_ci_workflow.py --check
```

Run the same validation bundle used by CI with:

```powershell
python .\scripts\run_ci_checks.py
```
