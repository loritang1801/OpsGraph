from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ProductCiConfig:
    workflow_name: str
    workflow_filename: str
    install_spec: str
    schema_generator_script: str | None = None
    schema_paths: tuple[str, ...] = ()
    smoke_commands: tuple[tuple[str, ...], ...] = ()
    path_filters: tuple[str, ...] = (
        ".github/workflows/*.yml",
        "pyproject.toml",
        "scripts/**",
        "schemas/**",
        "src/**",
        "shared_core/**",
        "tests/**",
        "README.md",
        "INTEGRATIONS.md",
        "PROMPT_TOOL.md",
    )
    python_version: str = "3.12"
    ci_entry_script: str = "scripts/run_ci_checks.py"


def _materialize_command(command: Iterable[str], *, python_executable: str) -> list[str]:
    return [python_executable if token == "{python}" else token for token in command]


def _run(command: list[str], *, cwd: Path) -> None:
    print(f"+ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def render_github_actions_workflow(config: ProductCiConfig) -> str:
    normalized_ci_entry = config.ci_entry_script.replace("\\", "/")
    lines = [
        "# Generated from shared_core.agent_platform.product_ci.",
        "# Regenerate with: python .\\scripts\\render_ci_workflow.py",
        f"name: {config.workflow_name}",
        "",
        "on:",
        "  push:",
        "    paths:",
    ]
    for path_filter in config.path_filters:
        lines.append(f'      - "{path_filter}"')
    lines.extend(
        [
            "  pull_request:",
            "    paths:",
        ]
    )
    for path_filter in config.path_filters:
        lines.append(f'      - "{path_filter}"')
    lines.extend(
        [
            "  workflow_dispatch:",
            "",
            "jobs:",
            "  validate:",
            "    runs-on: ubuntu-latest",
            "",
            "    steps:",
            "      - name: Check Out Repository",
            "        uses: actions/checkout@v4",
            "",
            "      - name: Set Up Python",
            "        uses: actions/setup-python@v5",
            "        with:",
            f'          python-version: "{config.python_version}"',
            '          cache: "pip"',
            "",
            "      - name: Install Dependencies",
            "        run: |",
            "          python -m pip install --upgrade pip",
            f"          python -m pip install -e {config.install_spec}",
            "",
            "      - name: Run CI Checks",
            f"        run: python {normalized_ci_entry}",
            "",
        ]
    )
    return "\n".join(lines)


def write_github_actions_workflow(root: Path, config: ProductCiConfig) -> Path:
    workflow_path = root / Path(config.workflow_filename)
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(render_github_actions_workflow(config), encoding="utf-8")
    return workflow_path


def check_github_actions_workflow(root: Path, config: ProductCiConfig) -> None:
    workflow_path = root / Path(config.workflow_filename)
    expected = render_github_actions_workflow(config)
    actual = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else None
    if actual != expected:
        raise ValueError(
            f"Generated workflow drift detected for {workflow_path}. "
            "Run python .\\scripts\\render_ci_workflow.py to regenerate."
        )


def run_ci_checks(root: Path, config: ProductCiConfig) -> int:
    python_executable = sys.executable
    check_github_actions_workflow(root, config)
    _run([python_executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=root)
    if config.schema_generator_script is not None:
        _run([python_executable, config.schema_generator_script], cwd=root)
    git = shutil.which("git")
    if git is not None and config.schema_paths:
        _run([git, "diff", "--exit-code", "--", *config.schema_paths], cwd=root)
    elif config.schema_paths:
        print("git not found; skipping schema workspace diff check")
    for command in config.smoke_commands:
        _run(_materialize_command(command, python_executable=python_executable), cwd=root)
    return 0
