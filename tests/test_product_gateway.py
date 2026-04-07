from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.product_gateway import OpsGraphProductModelGateway


class _FailingPrimaryGateway:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def generate(self, *, assembled_prompt):
        del assembled_prompt
        raise self._error


class _RecordingFallbackGateway:
    def __init__(self, response) -> None:
        self.calls = 0
        self.response = response

    def generate(self, *, assembled_prompt):
        del assembled_prompt
        self.calls += 1
        return self.response


class OpsGraphProductGatewayTests(unittest.TestCase):
    def test_auto_mode_runtime_failure_falls_back_and_updates_capability(self) -> None:
        fallback_gateway = _RecordingFallbackGateway({"gateway": "fallback"})
        gateway = OpsGraphProductModelGateway(
            primary_gateway=_FailingPrimaryGateway(RuntimeError("remote failure")),
            fallback_gateway=fallback_gateway,
            allow_fallback=True,
        )

        response = gateway.generate(assembled_prompt=SimpleNamespace())
        capability = gateway.describe_capability()

        self.assertEqual(response, {"gateway": "fallback"})
        self.assertEqual(fallback_gateway.calls, 1)
        self.assertEqual(capability["requested_mode"], "auto")
        self.assertEqual(capability["effective_mode"], "local")
        self.assertEqual(capability["backend_id"], "heuristic-local")
        self.assertEqual(capability["fallback_reason"], "MODEL_PROVIDER_REQUEST_FAILED")
        self.assertEqual(capability["details"]["fallback_enabled"], True)
        self.assertEqual(capability["details"]["fallback_policy_source"], "explicit")
        self.assertEqual(capability["details"]["strict_remote_required"], False)
        self.assertEqual(capability["details"]["last_primary_error"], "RuntimeError")

    def test_runtime_failure_raises_when_fallback_disabled(self) -> None:
        fallback_gateway = _RecordingFallbackGateway({"gateway": "fallback"})
        gateway = OpsGraphProductModelGateway(
            primary_gateway=_FailingPrimaryGateway(RuntimeError("remote failure")),
            fallback_gateway=fallback_gateway,
            allow_fallback=False,
        )

        with self.assertRaisesRegex(RuntimeError, "remote failure"):
            gateway.generate(assembled_prompt=SimpleNamespace())

        capability = gateway.describe_capability()
        self.assertEqual(fallback_gateway.calls, 0)
        self.assertEqual(capability["requested_mode"], "auto")
        self.assertEqual(capability["effective_mode"], "openai")
        self.assertEqual(capability["backend_id"], "openai-responses")
        self.assertIsNone(capability["fallback_reason"])
        self.assertEqual(capability["details"]["fallback_enabled"], False)
        self.assertEqual(capability["details"]["fallback_policy_source"], "explicit")
        self.assertEqual(capability["details"]["strict_remote_required"], True)
        self.assertEqual(capability["details"]["last_primary_error"], "RuntimeError")

    def test_invalid_model_fallback_env_raises_configuration_error(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPSGRAPH_MODEL_PROVIDER": "auto",
                "OPSGRAPH_MODEL_ALLOW_FALLBACK": "maybe",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "INVALID_OPSGRAPH_MODEL_ALLOW_FALLBACK"):
                OpsGraphProductModelGateway(primary_gateway=object())
