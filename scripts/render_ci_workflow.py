from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_config import build_ci_config
from shared_core.agent_platform.product_ci import check_github_actions_workflow, write_github_actions_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render or validate the OpsGraph GitHub Actions workflow.")
    parser.add_argument("--check", action="store_true", help="Validate that the committed workflow matches the shared template.")
    return parser.parse_args()


def main() -> int:
    config = build_ci_config()
    if parse_args().check:
        check_github_actions_workflow(ROOT, config)
        return 0
    write_github_actions_workflow(ROOT, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
