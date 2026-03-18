# OpsGraph Source

Product-layer OpsGraph application code lives under `src/opsgraph_app/`.

Current implementation includes:

- `bootstrap.py`: shared-runtime wiring and app factories
- `service.py`: incident, approval, comms, retrospective, and replay orchestration
- `repository.py`: SQLAlchemy-backed incident and replay repository
- `routes.py`: FastAPI route layer and shared response envelopes
- `replay_fixtures.py`: fixture seeding for replay execution
- `replay_reports.py`: JSON/Markdown replay report artifact export
