from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
_ENV_LOADED = False


def load_local_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            if not normalized_key:
                continue
            normalized_value = value.strip()
            if len(normalized_value) >= 2 and (
                (normalized_value[0] == normalized_value[-1] == '"')
                or (normalized_value[0] == normalized_value[-1] == "'")
            ):
                normalized_value = normalized_value[1:-1]
            os.environ.setdefault(normalized_key, normalized_value)
    _ENV_LOADED = True


def ensure_src_on_path() -> Path:
    load_local_env()
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return SRC


def _local_data_dir() -> Path:
    directory = ROOT / ".local"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_database_path() -> Path:
    return _local_data_dir() / "opsgraph.db"


def resolve_database_url(explicit: str | None = None) -> str:
    load_local_env()
    if explicit:
        return explicit
    configured = os.getenv("OPSGRAPH_DATABASE_URL")
    if configured:
        return configured
    return f"sqlite+pysqlite:///{default_database_path().as_posix()}"
