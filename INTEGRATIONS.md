# OpsGraph Remote Integrations

`OpsGraph` supports optional remote HTTP providers for selected read-only tools. These providers are product-scoped integrations layered on top of the shared workflow/runtime model.

Canonical request and response fixtures live under `tests/fixtures/remote_provider_contracts/`.

Generated JSON Schema files live under `schemas/remote_provider_contracts/`.

Regenerate them after contract changes with:

```powershell
python .\scripts\generate_remote_provider_schemas.py
```

The generated GitHub Actions workflow is rendered by `python .\scripts\render_ci_workflow.py`. Use `python .\scripts\render_ci_workflow.py --check` to verify the committed workflow is still in sync with the shared template.

That generated workflow runs `python .\scripts\run_ci_checks.py`, which validates tests, schema drift, and the demo workflow in one pass.

## Common Configuration Pattern

Each remote provider follows the same env prefix pattern:

- `OPSGRAPH_<PROVIDER>_PROVIDER`: `auto`, `local`, or `http`
- `OPSGRAPH_<PROVIDER>_URL_TEMPLATE`: required for `http`
- `OPSGRAPH_<PROVIDER>_AUTH_TOKEN`: optional bearer token
- `OPSGRAPH_<PROVIDER>_HEADERS_JSON`: optional extra headers
- `OPSGRAPH_<PROVIDER>_CONNECTION_ID`: optional provenance identifier
- `OPSGRAPH_<PROVIDER>_BACKEND_ID`: optional runtime-capabilities label override
- `OPSGRAPH_<PROVIDER>_TIMEOUT_SECONDS`: optional timeout override

Mode behavior:

- `local`: skip remote HTTP and use the built-in heuristic/local adapter
- `auto`: use remote HTTP when `..._URL_TEMPLATE` is configured; otherwise fall back to local
- `http`: require remote HTTP configuration and fail fast if missing or invalid

## Deployment Lookup

Env prefix: `OPSGRAPH_DEPLOYMENT_LOOKUP_*`

Canonical request template variables:

- `{service_id}`
- `{incident_id}`
- `{limit}`

Canonical request fixture:

- `tests/fixtures/remote_provider_contracts/deployment_lookup_request.json`

Canonical response fixture:

- `tests/fixtures/remote_provider_contracts/deployment_lookup_response.json`

Canonical response shape:

```json
{
  "deployments": [
    {
      "deployment_id": "deploy-remote-1",
      "commit_ref": "9f8e7d6c5b4a",
      "actor": "release-bot",
      "deployed_at": "2026-03-27T01:02:03Z"
    }
  ]
}
```

Accepted aliases are still tolerated by the parser, but the canonical contract above is what new providers should emit.

## Service Registry Lookup

Env prefix: `OPSGRAPH_SERVICE_REGISTRY_*`

Canonical request template variables:

- `{service_id}`
- `{search_query}`
- `{limit}`

Canonical request fixture:

- `tests/fixtures/remote_provider_contracts/service_registry_request.json`

Canonical response fixture:

- `tests/fixtures/remote_provider_contracts/service_registry_response.json`

Canonical response shape:

```json
{
  "services": [
    {
      "service_id": "checkout-api",
      "name": "Checkout API",
      "owner_team": "payments-sre",
      "dependency_names": ["postgres", "redis"],
      "runbook_refs": ["runbook-checkout-api-rollback"]
    }
  ]
}
```

## Runbook Search

Env prefix: `OPSGRAPH_RUNBOOK_SEARCH_*`

Canonical request template variables:

- `{service_id}`
- `{query}`
- `{limit}`

Canonical request fixture:

- `tests/fixtures/remote_provider_contracts/runbook_search_request.json`

Canonical response fixture:

- `tests/fixtures/remote_provider_contracts/runbook_search_response.json`

Canonical response shape:

```json
{
  "runbooks": [
    {
      "runbook_id": "runbook-checkout-api-rollback",
      "title": "Rollback Checkout API safely",
      "excerpt": "Rollback the latest checkout-api deployment and verify service health.",
      "score": 0.98
    }
  ]
}
```

## Runtime Introspection

`GET /api/v1/opsgraph/runtime-capabilities` reports the effective mode and backend for each provider.

`GET /health` returns a lighter runtime summary with:

- `model_provider_mode`
- `model_backend_id`
- `tooling_modes`
- `tooling_backends`

Use the full capability route for admin diagnostics and `/health` for lightweight runtime visibility.
