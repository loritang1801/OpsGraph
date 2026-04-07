from __future__ import annotations

import os
import sys
from pathlib import Path


def load_shared_agent_platform():
    preferred_source = (os.getenv("OPSGRAPH_SHARED_CORE_SOURCE") or "vendored").strip().lower()
    if preferred_source not in {"vendored", "workspace"}:
        raise ValueError("OPSGRAPH_SHARED_CORE_SOURCE must be 'vendored' or 'workspace'")
    repo_root = Path(__file__).resolve().parents[2]
    workspace_shared = Path(__file__).resolve().parents[3] / "SharedAgentCore"
    if preferred_source == "workspace":
        if str(workspace_shared) not in sys.path:
            sys.path.insert(0, str(workspace_shared))
        try:
            import agent_platform  # type: ignore

            return agent_platform
        except ImportError:
            pass
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        import shared_core.agent_platform as agent_platform  # type: ignore

        return agent_platform
    except ImportError:
        if preferred_source == "vendored":
            if str(workspace_shared) not in sys.path:
                sys.path.insert(0, str(workspace_shared))
            import agent_platform  # type: ignore

            return agent_platform
        raise
