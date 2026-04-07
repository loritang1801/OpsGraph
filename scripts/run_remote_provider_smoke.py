from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsgraph_app.remote_provider_smoke import (
    available_smoke_providers,
    run_remote_provider_smoke_suite,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live smoke checks for configured OpsGraph remote providers."
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=available_smoke_providers(include_write=True),
        help="Provider to probe. Repeat to probe multiple providers.",
    )
    parser.add_argument(
        "--include-write",
        action="store_true",
        help="Include write-capable providers in the default probe set.",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Allow executing write-capable providers such as comms_publish.",
    )
    parser.add_argument(
        "--require-configured",
        action="store_true",
        help="Exit non-zero when any selected provider is skipped because remote mode is inactive.",
    )
    parser.add_argument("--service-id", default="checkout-api")
    parser.add_argument("--incident-id", default="incident-1")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--search-query", default="checkout api")
    parser.add_argument("--runbook-query", default="rollback elevated 5xx")
    parser.add_argument("--draft-id", default="draft-1")
    parser.add_argument("--channel-type", default="internal_slack")
    parser.add_argument("--title", default="OpsGraph remote provider smoke")
    parser.add_argument("--body-markdown", default="Smoke validation for remote provider delivery.")
    parser.add_argument("--fact-set-version", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_remote_provider_smoke_suite(
        providers=list(args.provider) if args.provider else None,
        include_write=args.include_write,
        allow_write=args.allow_write,
        require_configured=args.require_configured,
        params={
            "service_id": args.service_id,
            "incident_id": args.incident_id,
            "limit": args.limit,
            "search_query": args.search_query,
            "runbook_query": args.runbook_query,
            "draft_id": args.draft_id,
            "channel_type": args.channel_type,
            "title": args.title,
            "body_markdown": args.body_markdown,
            "fact_set_version": args.fact_set_version,
        },
    )
    print(json.dumps(payload, indent=2))
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
