from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def create_repo_tempdir(prefix: str) -> Path:
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = temp_root / f"{prefix}{uuid4().hex[:12]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate


def cleanup_repo_tempdir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
