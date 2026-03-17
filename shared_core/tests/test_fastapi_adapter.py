from __future__ import annotations

import unittest

from agent_platform import (
    InMemoryOutboxStore,
    PromptAssemblyService,
    StaticModelGateway,
    WorkflowApiService,
    WorkflowExecutionService,
    build_default_runtime_catalog,
    build_workflow_registry,
    create_fastapi_app,
)
from agent_platform.errors import FastAPIUnavailableError


class FastAPIAdapterTests(unittest.TestCase):
    def test_create_fastapi_app_raises_when_fastapi_is_missing(self) -> None:
        api_service = WorkflowApiService(
            build_workflow_registry(),
            WorkflowExecutionService(
                PromptAssemblyService(build_default_runtime_catalog()),
                model_gateway=StaticModelGateway(),
                outbox_store=InMemoryOutboxStore(),
            ),
        )

        with self.assertRaises(FastAPIUnavailableError):
            create_fastapi_app(api_service)


if __name__ == "__main__":
    unittest.main()
