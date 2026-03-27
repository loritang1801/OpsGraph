from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_config import build_ci_config
from shared_core.agent_platform.product_ci import run_ci_checks


def main() -> int:
    return run_ci_checks(ROOT, build_ci_config())


if __name__ == "__main__":
    raise SystemExit(main())
