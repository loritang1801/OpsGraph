from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_core.agent_platform.product_ci import ProductCiConfig


def build_ci_config() -> ProductCiConfig:
    return ProductCiConfig(
        workflow_name="OpsGraph CI",
        workflow_filename=".github/workflows/opsgraph-ci.yml",
        install_spec=".[api]",
        schema_generator_script="scripts/generate_remote_provider_schemas.py",
        schema_paths=("schemas/remote_provider_contracts",),
        smoke_commands=(
            ("{python}", "scripts/run_demo_workflow.py"),
            ("{python}", "scripts/run_remote_provider_smoke.py"),
            (
                "{python}",
                "scripts/run_replay_worker.py",
                "--seed-run",
                "--supervise",
                "--iterations",
                "2",
                "--max-idle-polls",
                "1",
            ),
        ),
        path_filters=(
            ".github/workflows/opsgraph-ci.yml",
            "pyproject.toml",
            "scripts/**",
            "schemas/**",
            "src/**",
            "shared_core/**",
            "tests/**",
            "README.md",
            "INTEGRATIONS.md",
            "PROMPT_TOOL.md",
        ),
    )
