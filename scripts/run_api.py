from __future__ import annotations

import argparse

from _local_runtime import ensure_src_on_path, resolve_database_url

ensure_src_on_path()

import uvicorn

from opsgraph_app.app import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OpsGraph FastAPI app against a local database.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database-url", help="Optional SQLAlchemy database URL.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(database_url=resolve_database_url(args.database_url))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
