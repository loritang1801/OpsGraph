# OpsGraph Tests

Current OpsGraph tests cover:

- bootstrap and FastAPI factory wiring
- alert intake and incident mutation flows
- approval, comms, resolve/close, and postmortem behavior
- replay case, replay run, baseline, execution, and evaluation coverage
- route helper and error mapping behavior

Run from the repo root with:

```powershell
python -m unittest discover -s tests -t .
```
