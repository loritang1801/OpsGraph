from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_platform import (
    InMemoryOutboxStore,
    PromptAssemblyService,
    StaticModelGateway,
    WorkflowApiService,
    WorkflowExecutionService,
    attach_service_lifecycle,
    build_managed_fastapi_app,
    build_default_runtime_catalog,
    build_workflow_registry,
    create_fastapi_app,
)
from agent_platform.errors import FastAPIUnavailableError


class FastAPIAdapterTests(unittest.TestCase):
    def test_build_managed_fastapi_app_closes_service_when_app_factory_fails(self) -> None:
        calls: list[str] = []

        class _ClosableService:
            def close(self) -> None:
                calls.append("closed")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            build_managed_fastapi_app(
                service_factory=_ClosableService,
                app_factory=lambda service: (_ for _ in ()).throw(RuntimeError("boom")),
            )

        self.assertEqual(calls, ["closed"])

    def test_attach_service_lifecycle_sets_state_and_shutdown_callback(self) -> None:
        calls: list[str] = []

        class _ClosableService:
            def close(self) -> None:
                calls.append("closed")

        app = SimpleNamespace(
            state=SimpleNamespace(),
            router=SimpleNamespace(on_shutdown=[]),
        )
        service = _ClosableService()

        attach_service_lifecycle(app, service=service, state_attr="service_ref")

        self.assertIs(app.state.service_ref, service)
        self.assertEqual(len(app.router.on_shutdown), 1)

        app.router.on_shutdown[0]()

        self.assertEqual(calls, ["closed"])

    def test_create_fastapi_app_raises_when_fastapi_is_missing(self) -> None:
        api_service = WorkflowApiService(
            build_workflow_registry(),
            WorkflowExecutionService(
                PromptAssemblyService(build_default_runtime_catalog()),
                model_gateway=StaticModelGateway(),
                outbox_store=InMemoryOutboxStore(),
            ),
        )

        with patch.dict(sys.modules, {"fastapi": None}):
            with self.assertRaises(FastAPIUnavailableError):
                create_fastapi_app(api_service)


if __name__ == "__main__":
    unittest.main()
