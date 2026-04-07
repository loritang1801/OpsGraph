from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path


_PAGE_TEMPLATE_PATH = Path(__file__).with_name("replay_worker_monitor_page.template.html")


@lru_cache(maxsize=1)
def _load_page_template() -> str:
    return _PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")


def render_replay_worker_monitor_html() -> str:
    title = html.escape("OpsGraph Replay Worker Monitor")
    return _load_page_template().format(title=title)
