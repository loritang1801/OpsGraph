from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_platform.product_ci import (
    ProductCiConfig,
    check_github_actions_workflow,
    render_github_actions_workflow,
    write_github_actions_workflow,
)


class ProductCiTests(unittest.TestCase):
    def test_rendered_workflow_includes_install_spec_and_path_filters(self) -> None:
        config = ProductCiConfig(
            workflow_name="Demo Product CI",
            workflow_filename=".github/workflows/demo-ci.yml",
            install_spec=".[api]",
            schema_generator_script="scripts/generate_demo_schemas.py",
            schema_paths=("schemas/demo",),
            smoke_commands=(("{python}", "scripts/run_demo.py"),),
            path_filters=("src/**", "shared_core/**", "tests/**"),
        )

        rendered = render_github_actions_workflow(config)

        self.assertIn("name: Demo Product CI", rendered)
        self.assertIn('      - "shared_core/**"', rendered)
        self.assertIn("python -m pip install -e .[api]", rendered)
        self.assertIn("run: python scripts/run_ci_checks.py", rendered)

    def test_write_and_check_generated_workflow(self) -> None:
        config = ProductCiConfig(
            workflow_name="Demo Product CI",
            workflow_filename=".github/workflows/demo-ci.yml",
            install_spec=".[api]",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_path = write_github_actions_workflow(root, config)

            self.assertTrue(workflow_path.exists())
            check_github_actions_workflow(root, config)

            workflow_path.write_text(workflow_path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Generated workflow drift detected"):
                check_github_actions_workflow(root, config)


if __name__ == "__main__":
    unittest.main()
