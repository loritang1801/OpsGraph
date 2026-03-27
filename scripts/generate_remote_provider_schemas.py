from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.remote_provider_schemas import write_remote_provider_schema_documents


def main() -> int:
    written_paths = write_remote_provider_schema_documents()
    print(
        json.dumps(
            {
                "written": [str(path.relative_to(ROOT)) for path in written_paths],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
