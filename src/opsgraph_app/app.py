from __future__ import annotations

from .bootstrap import build_fastapi_app


def create_app(*, database_url: str | None = None):
    return build_fastapi_app(database_url=database_url)
