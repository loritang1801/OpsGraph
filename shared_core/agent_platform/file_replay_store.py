from __future__ import annotations

import base64
from pathlib import Path

from .errors import RegistryLookupError
from .replay import ReplayFixture, ReplayFixtureStore


def _fixture_filename(fixture_key: str) -> str:
    encoded = base64.urlsafe_b64encode(fixture_key.encode("utf-8")).decode("ascii")
    return f"{encoded}.json"


class FileReplayFixtureStore(ReplayFixtureStore):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, fixture: ReplayFixture) -> None:
        target = self.root / _fixture_filename(fixture.fixture_key)
        target.write_text(fixture.model_dump_json(indent=2), encoding="utf-8")

    def get(self, fixture_key: str) -> ReplayFixture:
        target = self.root / _fixture_filename(fixture_key)
        if not target.exists():
            raise RegistryLookupError(f"Unknown replay fixture: {fixture_key}")
        return ReplayFixture.model_validate_json(target.read_text(encoding="utf-8"))

    def list_keys(self) -> list[str]:
        return [
            ReplayFixture.model_validate_json(path.read_text(encoding="utf-8")).fixture_key
            for path in sorted(self.root.glob("*.json"))
        ]
